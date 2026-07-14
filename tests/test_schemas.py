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


def test_null_attributes_and_aliases_are_treated_as_empty():
    """
    2026-07-14生产环境真实422错误：Java的Map<String,Object> attributes字段没值时
    序列化成显式null，之前的null兜底逻辑只处理了list没处理裸dict类型，校验直接失败。
    """
    sample = {
        "items": [
            {
                "mention": {
                    "mentionId": "m1",
                    "name": "CENTCOM",
                    "canonicalName": "U.S. Central Command",
                    "type": "organization",
                    "aliases": None,
                    "attributes": None,
                },
                "candidates": [
                    {
                        "entityId": "e1",
                        "canonicalName": "U.S. Central Command",
                        "type": "organization",
                        "aliases": None,
                        "attributes": None,
                        "score": 0.9,
                        "retrievalChannels": None,
                    }
                ],
                "context": None,
            }
        ],
        "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
    }
    request = ResolveBatchRequest.model_validate(sample)
    assert request.items[0].mention.attributes == {}
    assert request.items[0].mention.aliases == []
    assert request.items[0].candidates[0].attributes == {}
    assert request.items[0].candidates[0].retrieval_channels == []


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


def test_422_validation_errors_are_logged_with_detail(caplog):
    """
    FastAPI默认422只把detail塞进响应体，不打印到容器日志，导致排查问题两边（调用方+服务本身）
    都看不到具体原因。这里验证自定义的validation_exception_handler确实把详细的校验错误
    和原始请求体打进了服务自己的日志，以后422发生时不用再靠猜。
    """
    import logging

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with caplog.at_level(logging.ERROR):
        resp = client.post("/resolve_batch", json={"items": "not-a-list", "strategy": {}})

    assert resp.status_code == 422
    assert any("422 Unprocessable Entity" in record.message for record in caplog.records)
    assert any("not-a-list" in record.message for record in caplog.records)


def test_candidate_with_null_canonical_name_from_vector_only_recall():
    """
    2026-07-14生产环境真实422错误：纯向量召回（VECTOR_INDEX，没有同时命中NAME_INDEX）的
    候选实体，后端目前不一定会额外查一次canonicalName，这种情况下这个字段是null，
    T3必须能正常处理，不能校验失败。这里用真实报错数据里的一条候选原样复现。
    """
    sample = {
        "items": [
            {
                "mention": {
                    "mentionId": "m2",
                    "name": "Sabah",
                    "canonicalName": "Sabah",
                    "type": "location",
                    "aliases": [],
                    "attributes": {},
                },
                "candidates": [
                    {
                        "entityId": "2c15dbf7-f0df-3f6b-acd0-3ff56028fc06",
                        "canonicalName": None,
                        "type": "location",
                        "aliases": [],
                        "importanceScore": None,
                        "attributes": {},
                        "score": 0.7,
                        "retrievalChannels": ["VECTOR_INDEX"],
                    }
                ],
                "context": {
                    "contentId": "bb3b8500-e65d-4f51-bbb0-d40274531a59",
                    "platform": "reddit",
                    "textWindow": "Sabah",
                    "language": "en",
                },
            }
        ],
        "strategy": {"autoMergeThreshold": 0.9, "reviewThreshold": 0.6},
    }
    request = ResolveBatchRequest.model_validate(sample)
    assert request.items[0].candidates[0].canonical_name is None
