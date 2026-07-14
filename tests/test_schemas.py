"""
基础冒烟测试：schema 解析 + sanitize 兜底逻辑，不依赖真实大模型调用。
"""
import json

from app.schemas.resolve_batch import ResolveBatchRequest, ResolveBatchResponse, ResolveResult
from app.services.resolve_service import _build_fallback_response, _sanitize


def test_request_parses_real_scenario_with_candidates():
    sample = {
        "items": [
            {
                "mention": {
                    "mentionId": "m1",
                    "name": "CENTCOM",
                    "canonicalName": "U.S. Central Command",
                    "type": "organization",
                    "span": {"start": 0, "end": 7},
                    "aliases": ["U.S. Central Command"],
                    "attributes": {"organizationCategories": ["state_institution"]},
                },
                "candidates": [
                    {
                        "entityId": "3f8a2b1c-0000-0000-0000-000000000001",
                        "canonicalName": "U.S. Central Command",
                        "type": "organization",
                        "aliases": ["CENTCOM", "美国中央司令部"],
                        "importanceScore": 92.0,
                        "attributes": {},
                        "score": 0.94,
                        "retrievalChannels": ["NAME_INDEX", "VECTOR_INDEX"],
                    }
                ],
                "context": {
                    "contentId": "content-1",
                    "platform": "reddit",
                    "textWindow": "CENTCOM announced a new exercise",
                    "language": "en",
                },
            }
        ],
        "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
    }
    request = ResolveBatchRequest.model_validate(sample)
    assert request.items[0].mention.mention_id == "m1"
    assert request.items[0].candidates[0].entity_id == "3f8a2b1c-0000-0000-0000-000000000001"
    assert request.strategy.auto_merge_threshold == 0.9


def test_request_with_empty_candidates_is_valid():
    request = ResolveBatchRequest.model_validate(
        {
            "items": [
                {
                    "mention": {
                        "mentionId": "m1",
                        "name": "Some New Org",
                        "canonicalName": "Some New Org",
                        "type": "organization",
                        "aliases": [],
                        "attributes": {},
                    },
                    "candidates": [],
                }
            ],
            "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
        }
    )
    assert request.items[0].candidates == []


def test_response_round_trips_camel_case():
    sample = {
        "results": [
            {
                "mentionId": "m1",
                "action": "MERGE",
                "matchedEntityId": "3f8a2b1c-0000-0000-0000-000000000001",
                "score": 0.96,
                "confidence": 0.96,
                "matchMethod": "exact_name_alias",
                "reason": "canonicalName一致，aliases高度重叠",
            }
        ],
        "modelVersion": "t3-resolve-v1.0",
    }
    response = ResolveBatchResponse.model_validate(sample)
    assert response.results[0].matched_entity_id == "3f8a2b1c-0000-0000-0000-000000000001"
    dumped = json.loads(response.model_dump_json(by_alias=True))
    assert dumped["results"][0]["matchedEntityId"] == "3f8a2b1c-0000-0000-0000-000000000001"


def test_fallback_response_creates_for_all_items():
    request = ResolveBatchRequest.model_validate(
        {
            "items": [
                {"mention": {"mentionId": "m1", "name": "A", "canonicalName": "A", "type": "person"}, "candidates": []},
                {"mention": {"mentionId": "m2", "name": "B", "canonicalName": "B", "type": "organization"}, "candidates": []},
            ],
            "strategy": {},
        }
    )
    response = _build_fallback_response(request)
    assert len(response.results) == 2
    assert all(r.action == "CREATE" for r in response.results)
    assert all(r.matched_entity_id is None for r in response.results)


def test_sanitize_downgrades_merge_with_invalid_entity_id_to_create():
    """
    大模型编造了一个不在候选列表里的matchedEntityId，应该被降级成CREATE，
    不能让一个不存在的entityId真的被当作合并目标传给后端。
    """
    request = ResolveBatchRequest.model_validate(
        {
            "items": [
                {
                    "mention": {"mentionId": "m1", "name": "A", "canonicalName": "A", "type": "person"},
                    "candidates": [
                        {"entityId": "real-id-1", "canonicalName": "A", "type": "person", "score": 0.9}
                    ],
                }
            ],
            "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
        }
    )
    bad_response = ResolveBatchResponse(
        results=[
            ResolveResult(
                mention_id="m1",
                action="MERGE",
                matched_entity_id="hallucinated-id-that-does-not-exist",
                score=0.95,
                confidence=0.95,
            )
        ],
        model_version="t3-resolve-v1.0",
    )
    sanitized = _sanitize(bad_response, request)
    assert sanitized.results[0].action == "CREATE"
    assert sanitized.results[0].matched_entity_id is None


def test_sanitize_fills_in_missing_mention_results():
    """大模型漏掉了某个mention的判断结果，sanitize应该给它补一条兜底CREATE。"""
    request = ResolveBatchRequest.model_validate(
        {
            "items": [
                {"mention": {"mentionId": "m1", "name": "A", "canonicalName": "A", "type": "person"}, "candidates": []},
                {"mention": {"mentionId": "m2", "name": "B", "canonicalName": "B", "type": "person"}, "candidates": []},
            ],
            "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
        }
    )
    incomplete_response = ResolveBatchResponse(
        results=[ResolveResult(mention_id="m1", action="CREATE", score=0.0, confidence=0.0)],
        model_version="t3-resolve-v1.0",
    )
    sanitized = _sanitize(incomplete_response, request)
    mention_ids = {r.mention_id for r in sanitized.results}
    assert mention_ids == {"m1", "m2"}


def test_llm_client_limits_concurrent_requests(monkeypatch):
    """
    T1/T2/T3共用同一个vLLM实例，2026-07-14生产环境真实事故（并发请求太多把vLLM的GPU显存
    压爆导致引擎崩溃）同样会影响T3，这里同步补上跟其他两个项目一样的并发限制验证。
    """
    import threading
    import time
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("LLM_MAX_CONCURRENT_REQUESTS", "2")
    from app.llm_client import LlmClient

    with patch("app.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]

        concurrent_count = [0]
        max_concurrent = [0]
        lock = threading.Lock()

        def slow_create(*args, **kwargs):
            with lock:
                concurrent_count[0] += 1
                max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            time.sleep(0.1)
            with lock:
                concurrent_count[0] -= 1
            return mock_response

        mock_client.chat.completions.create.side_effect = slow_create

        client = LlmClient()
        threads = [threading.Thread(target=client.call_json, args=("sys", "user")) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_concurrent[0] <= 2
