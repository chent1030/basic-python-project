"""MemoryIngestionService 单元测试：单条 / 幂等 / backfill / supersede / 失败兜底。"""
from __future__ import annotations

import json

import pytest

from app.skill.memory.application.ingestion_service import (
    IngestionSummary,
    MemoryIngestionService,
)
from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding
from app.skill.memory.infrastructure.repository import MemoryRepository


@pytest.fixture
def ingestion_service(memory_repository, embedding_client, memory_session_factory):
    return MemoryIngestionService(
        memory_repository, embedding_client, java_client=None
    )


@pytest.fixture
def ingestion_service_with_java(memory_repository, embedding_client):
    from tests.skill.conftest import FakeJavaClient
    java = FakeJavaClient()
    svc = MemoryIngestionService(memory_repository, embedding_client, java_client=java)
    return svc, java


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_ingest_adjudication_basic(ingestion_service: MemoryIngestionService):
    payload = {
        "id": 11,
        "issue_id": 100,
        "version_no": 1,
        "category_l1_id": 3,
        "factory": "A",
        "area": "B",
        "severity": 4,
        "decision": "APPROVE",
        "ai_relation": "STRONG",
        "reason": "evidence enough",
        "reviewer": "alice",
    }
    saved = await ingestion_service.ingest_adjudication(payload)
    assert saved.id is not None
    assert saved.source_table == "ADJUDICATION"
    assert saved.source_id == 11
    assert saved.payload["decision"] == "APPROVE"
    assert saved.payload["reviewer"] == "alice"


@pytest.mark.asyncio
async def test_ingest_adjudication_idempotent(ingestion_service: MemoryIngestionService):
    p = {"id": 11, "issue_id": 100, "decision": "APPROVE"}
    s1 = await ingestion_service.ingest_adjudication(p)
    s2 = await ingestion_service.ingest_adjudication({**p, "decision": "REJECT"})
    assert s1.id == s2.id
    assert s2.payload["decision"] == "REJECT"


@pytest.mark.asyncio
async def test_ingest_event_and_issue(ingestion_service: MemoryIngestionService):
    e = await ingestion_service.ingest_event({"id": 1, "issue_id": 100, "payload": {"kind": "x"}})
    i = await ingestion_service.ingest_issue({"id": 1, "issue_id": 100, "payload": {"summary": "s"}})
    assert e.source_table == "EVENT"
    assert i.source_table == "ISSUE"


@pytest.mark.asyncio
async def test_backfill_routes_to_ingestor(ingestion_service_with_java):
    svc, java = ingestion_service_with_java
    java._kinds["adjudications"] = [
        {"id": 1, "issue_id": 100, "decision": "APPROVE", "category_l1_id": 1, "factory": "A", "area": "B"},
        {"id": 2, "issue_id": 101, "decision": "REJECT", "category_l1_id": 1, "factory": "A", "area": "B"},
    ]
    java._kinds["events"] = [{"id": 1, "issue_id": 100, "payload": {"k": "v"}}]
    summary: IngestionSummary = await svc.backfill_since("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z")
    assert summary.adjudications == 2
    assert summary.events == 1
    assert summary.issues == 0
    assert summary.errors == []
    # verify the rows landed
    repo = svc._repo
    assert len(await repo.list_by_issue(issue_id=100)) >= 2


@pytest.mark.asyncio
async def test_backfill_handles_java_failure_gracefully(ingestion_service_with_java):
    svc, java = ingestion_service_with_java
    java.should_fail = True
    summary: IngestionSummary = await svc.backfill_since("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z")
    # kinds 全部失败 → 计数 0，但 errors 列表非空
    assert summary.adjudications == 0
    assert summary.errors
    # 仍然不抛异常
    payload = summary.to_dict()
    assert isinstance(payload, dict)
    assert "adjudications" in payload and "errors" in payload


@pytest.mark.asyncio
async def test_supersede_stale_patterns_marks_with_tag(ingestion_service: MemoryIngestionService):
    # 先 upsert 一条带 stale tag 的 entry；再调用 supersede_stale_patterns
    j = await ingestion_service.ingest_adjudication({
        "id": 1, "issue_id": 100, "tags": ["stale", "old"]
    })
    # 入口注入 stale tag
    await ingestion_service.supersede_stale_patterns()
    rows = await ingestion_service._repo.list_by_issue(issue_id=100, include_inactive=True)
    by_id = {r.id: r for r in rows}
    assert by_id[j.id].is_active is False


@pytest.mark.asyncio
async def test_ingest_uses_payload_field_when_present(ingestion_service: MemoryIngestionService):
    custom = {"k": "v", "decision": "APPROVE"}
    e = await ingestion_service.ingest_adjudication({
        "id": 7, "issue_id": 100, "payload": custom,
    })
    assert e.payload == custom


@pytest.mark.asyncio
async def test_ingestion_summary_to_dict():
    s = IngestionSummary(adjudications=2, events=1, issues=0, errors=["x"])
    d = s.to_dict()
    assert d == {"adjudications": 2, "events": 1, "issues": 0, "errors": ["x"]}


__all__ = []