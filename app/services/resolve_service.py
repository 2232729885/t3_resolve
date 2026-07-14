"""
POST /resolve_batch 的业务逻辑：整批 items 一次性拼进提示词 -> 调大模型 ->
解析成 ResolveBatchResponse，并把顶层 modelVersion 回填进每个 result。
"""
import logging

from pydantic import ValidationError

from app.llm_client import LlmCallError, get_llm_client
from app.prompts import RESOLVE_BATCH_SYSTEM_PROMPT
from app.schemas.resolve_batch import ResolveBatchRequest, ResolveBatchResponse, ResolveResult, VALID_ACTIONS

logger = logging.getLogger(__name__)

MODEL_VERSION = "t3-resolve-v1.0"


def _build_user_content(request: ResolveBatchRequest) -> str:
    strategy = request.strategy
    parts: list[str] = [
        f"autoMergeThreshold={strategy.auto_merge_threshold}, reviewThreshold={strategy.review_threshold}",
        f"Total items: {len(request.items)}",
        "",
    ]

    for idx, item in enumerate(request.items, start=1):
        m = item.mention
        parts.append(f"--- Item {idx} ---")
        parts.append(
            f"mentionId={m.mention_id}, name={m.name!r}, canonicalName={m.canonical_name!r}, "
            f"type={m.type}, aliases={m.aliases}, attributes={m.attributes}"
        )
        ctx = item.context
        if ctx is not None and ctx.text_window:
            parts.append(f"context.textWindow: {ctx.text_window}")

        if not item.candidates:
            parts.append("candidates: (none)")
        else:
            parts.append(f"candidates ({len(item.candidates)}):")
            for c in item.candidates:
                name_display = c.canonical_name if c.canonical_name else "(name unavailable, judge mainly by recallScore/retrievalChannels/attributes)"
                parts.append(
                    f"  - entityId={c.entity_id}, canonicalName={name_display!r}, type={c.type}, "
                    f"aliases={c.aliases}, importanceScore={c.importance_score}, "
                    f"attributes={c.attributes}, recallScore={c.score}, "
                    f"retrievalChannels={c.retrieval_channels}"
                )
        parts.append("")

    return "\n".join(parts)


def _build_fallback_response(request: ResolveBatchRequest) -> ResolveBatchResponse:
    """
    大模型调用整体失败时，每个mention都保守地判CREATE（不合并进任何已有实体），
    低置信度、needHumanReview之类的判断由上游backend自己根据confidence去处理。
    宁可多建一个待后续实体去重任务合并的新实体，也不要冒险错误合并进一个不相关的实体。
    """
    results = [
        ResolveResult(
            mention_id=item.mention.mention_id,
            action="CREATE",
            matched_entity_id=None,
            score=0.0,
            confidence=0.0,
            match_method=None,
            reason="LLM调用失败，兜底判定为CREATE，不冒险合并",
            model_version=MODEL_VERSION,
        )
        for item in request.items
    ]
    return ResolveBatchResponse(results=results, model_version=MODEL_VERSION)


def _sanitize(response: ResolveBatchResponse, request: ResolveBatchRequest) -> ResolveBatchResponse:
    """
    对大模型的输出做兜底校验，不完全信任它100%守规矩：
    - action不在MERGE/REVIEW/CREATE三者之内的，改成CREATE
    - action=CREATE但给了matchedEntityId的，清空（CREATE不应该有匹配实体）
    - action=MERGE/REVIEW但没给matchedEntityId、或者这个entityId根本不在候选列表里的，降级成CREATE
    - 结果里缺了某个mentionId的，补一条兜底CREATE，保证每个输入item都有对应输出
    """
    candidate_ids_by_mention = {
        item.mention.mention_id: {c.entity_id for c in item.candidates} for item in request.items
    }
    seen_mention_ids: set[str] = set()
    sanitized: list[ResolveResult] = []

    for result in response.results:
        if result.mention_id not in candidate_ids_by_mention:
            # 大模型编造了一个不存在的mentionId，直接丢弃这条
            logger.warning("Dropping result for unknown mentionId: %s", result.mention_id)
            continue
        seen_mention_ids.add(result.mention_id)

        if result.action not in VALID_ACTIONS:
            logger.warning("Invalid action %r for mentionId=%s, downgrading to CREATE", result.action, result.mention_id)
            result.action = "CREATE"
            result.matched_entity_id = None
        elif result.action == "CREATE":
            result.matched_entity_id = None
        elif result.action in ("MERGE", "REVIEW"):
            valid_ids = candidate_ids_by_mention.get(result.mention_id, set())
            if not result.matched_entity_id or result.matched_entity_id not in valid_ids:
                logger.warning(
                    "action=%s but matchedEntityId invalid for mentionId=%s, downgrading to CREATE",
                    result.action, result.mention_id,
                )
                result.action = "CREATE"
                result.matched_entity_id = None

        result.model_version = response.model_version or MODEL_VERSION
        sanitized.append(result)

    # 大模型漏掉的mentionId，补一条兜底CREATE，保证每个输入item都有输出
    for item in request.items:
        if item.mention.mention_id not in seen_mention_ids:
            logger.warning("LLM omitted result for mentionId=%s, filling in fallback CREATE", item.mention.mention_id)
            sanitized.append(
                ResolveResult(
                    mention_id=item.mention.mention_id,
                    action="CREATE",
                    matched_entity_id=None,
                    score=0.0,
                    confidence=0.0,
                    reason="模型未返回该mention的判断结果，兜底判定为CREATE",
                    model_version=response.model_version or MODEL_VERSION,
                )
            )

    response.results = sanitized
    return response


def resolve_batch(request: ResolveBatchRequest) -> ResolveBatchResponse:
    if not request.items:
        return ResolveBatchResponse(results=[], model_version=MODEL_VERSION)

    try:
        raw = get_llm_client().call_json(RESOLVE_BATCH_SYSTEM_PROMPT, _build_user_content(request))
        response = ResolveBatchResponse.model_validate(raw)
    except (LlmCallError, ValidationError) as exc:
        logger.error("resolve_batch failed, falling back to CREATE for all items: %s", exc)
        return _build_fallback_response(request)

    if not response.model_version:
        response.model_version = MODEL_VERSION
    return _sanitize(response, request)
