"""
`POST /resolve_batch` 请求/响应 schema。
对应课题四后端仓库 docs/T3实体消歧接口规约.md（唯一权威来源）。

T3 只做判断，不读写任何数据库。候选实体由后端查询后传入。这个接口同时服务
内容抽取（T2之后）和账号身份识别两种场景，输入结构完全一样，不需要区分调用方是谁。
"""
from __future__ import annotations

from typing import Optional

from app.schemas.common import CamelModel

# action 只能是这3种之一
VALID_ACTIONS = frozenset({"MERGE", "REVIEW", "CREATE"})


# ==================== 请求 ====================


class Span(CamelModel):
    start: int
    end: int


class Mention(CamelModel):
    mention_id: str
    name: str
    canonical_name: str
    type: str  # person | organization | event | location
    span: Optional[Span] = None
    aliases: list[str] = []
    attributes: dict = {}


class Candidate(CamelModel):
    entity_id: str
    # 允许为空——通过向量召回（VECTOR_INDEX）但没有同时命中ES名称匹配（NAME_INDEX）的候选，
    # 后端目前不一定会额外查一次实体的标准名，这种情况下这个字段会是null，
    # T3这边要能正常处理，不能直接校验失败
    canonical_name: Optional[str] = None
    type: str
    aliases: list[str] = []
    importance_score: Optional[float] = None
    attributes: dict = {}
    # 后端候选召回时算出的初步相似度分数（ES/Milvus检索分数），仅供参考，
    # T3应该结合mention/candidates的实际语义相似度重新判断，不能只看这个分数
    score: Optional[float] = None
    # NAME_INDEX（ES名称匹配）/ VECTOR_INDEX（向量召回）
    retrieval_channels: list[str] = []


class Context(CamelModel):
    # 内容ID或者账号ID（账号身份识别场景下装的是账号ID），只用于日志追踪，不影响判断逻辑
    content_id: Optional[str] = None
    platform: Optional[str] = None
    # mention出现的上下文文本片段，账号身份识别场景下是账号的bio
    text_window: Optional[str] = None
    language: Optional[str] = None


class ResolveItem(CamelModel):
    mention: Mention
    candidates: list[Candidate] = []
    context: Optional[Context] = None


class Strategy(CamelModel):
    auto_merge_threshold: float = 0.9
    review_threshold: float = 0.6


class ResolveBatchRequest(CamelModel):
    items: list[ResolveItem] = []
    strategy: Strategy = Strategy()


# ==================== 响应 ====================


class ResolveResult(CamelModel):
    mention_id: str
    action: str  # MERGE | REVIEW | CREATE
    matched_entity_id: Optional[str] = None
    score: float
    confidence: float
    match_method: Optional[str] = None
    reason: Optional[str] = None
    # 顶层modelVersion的内部传播字段，供下游审核记录使用，T3不需要主动填，
    # 由 resolve_service 统一从顶层modelVersion回填
    model_version: Optional[str] = None


class ResolveBatchResponse(CamelModel):
    results: list[ResolveResult] = []
    model_version: str = "t3-resolve-v1.0"
