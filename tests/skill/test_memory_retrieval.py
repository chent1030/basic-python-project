"""MemoryRetrievalService 单元测试：issue 上下文检索 + pattern 检索 + 提示词拼接。"""
from __future__ import annotations

import json

import pytest

from app.skill.memory.application.retrieval_service import (
    MemoryRetrievalService,
    RetrievalHint,
)
from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import EMBEDDING_DIM, MemoryEntry
from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding
from app.skill.memory.infrastructure.repository import MemoryRepository


@pytest.fixture
def retrieval_service(memory_repository: MemoryRepository, embedding_client):
    return MemoryRetrievalService(memory_repository, embedding_client)


# ---------------------------------------------------------------- helpers --


async def _seed_judgments(
    repo: MemoryRepository,
    *,
    issue_id: int | None = None,
    factory: str = "A",
    area: str = "B",
    category_l1_id: int | None = 1,
    n: int = 3,
    base_source_id: int = 1000,
    embedding_dir: list[float] | None = None,
):
    """往仓库塞 n 条 ADJUDICATION 记忆。"""
    if embedding_dir is None:
        embedding_dir = [0.1] * EMBEDDING_DIM
    for i in range(n):
        e = MemoryEntry(
            source_table=SourceTable.ADJUDICATION.value,
            source_id=base_source_id + i,
            issue_id=issue_id,
            category_l1_id=category_l1_id,
            factory=factory,
            area=area,
            embedding=list(embedding_dir),
            payload={"decision": "APPROVE", "reason": f"reason {i}"},
            tags=[],
        )
        await repo.upsert_judgment(e)


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_retrieve_context_for_issue_basic(
    retrieval_service: MemoryRetrievalService,
    memory_repository: MemoryRepository,
):
    await _seed_judgments(memory_repository, n=4, base_source_id=1000, issue_id=200)
    issue = {"id": 200, "factory": "A", "area": "B", "category_l1_id": 1}
    hints = await retrieval_service.retrieve_context_for_issue(issue, top_k=3)
    assert len(hints) == 3
    assert all(isinstance(h, RetrievalHint) for h in hints)
    # 返回值 score 是 float
    assert all(isinstance(h.score, float) for h in hints)


@pytest.mark.asyncio
async def test_retrieve_filters_by_factory_area(
    retrieval_service: MemoryRetrievalService,
    memory_repository: MemoryRepository,
):
    await _seed_judgments(memory_repository, n=2, base_source_id=1000, factory="A", area="B")
    await _seed_judgments(memory_repository, n=2, base_source_id=2000, factory="B", area="B")
    issue = {"factory": "A", "area": "B"}
    hints = await retrieval_service.retrieve_context_for_issue(issue, top_k=10)
    source_ids = {h.entry.source_id for h in hints}
    assert all(1000 <= s < 2000 for s in source_ids)


@pytest.mark.asyncio
async def test_retrieve_top_k_caps_results(
    retrieval_service: MemoryRetrievalService,
    memory_repository: MemoryRepository,
):
    await _seed_judgments(memory_repository, n=10, base_source_id=3000)
    hints = await retrieval_service.retrieve_context_for_issue({}, top_k=4)
    assert len(hints) == 4


@pytest.mark.asyncio
async def test_retrieve_kind_filter_scopes_to_events(
    retrieval_service: MemoryRetrievalService,
    memory_repository: MemoryRepository,
):
    # 同时塞 ADJUDICATION + EVENT
    for sid in (1, 2):
        await memory_repository.upsert_judgment(MemoryEntry(
            source_table=SourceTable.ADJUDICATION.value, source_id=sid,
            payload={"decision": "A"},
        ))
    for sid in (10, 20):
        await memory_repository.upsert_episode(MemoryEntry(
            source_table=SourceTable.EVENT.value, source_id=sid,
            payload={"kind": "x"},
        ))
    hints = await retrieval_service.retrieve_context_for_issue(
        {}, top_k=10, kind_filter=["EVENT"]
    )
    assert all(h.entry.source_table == "EVENT" for h in hints)


@pytest.mark.asyncio
async def test_retrieve_falls_back_on_empty(
    retrieval_service: MemoryRetrievalService,
):
    """空库检索 → 空列表（不阻断主流程）。"""
    hints = await retrieval_service.retrieve_context_for_issue({}, top_k=3)
    assert hints == []


@pytest.mark.asyncio
async def test_format_hints_for_prompt_json(
    retrieval_service: MemoryRetrievalService,
):
    """hints → JSON 字符串（空 → None）。"""
    assert retrieval_service.format_hints_for_prompt(None) is None
    assert retrieval_service.format_hints_for_prompt([]) is None
    fake_entry = MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=1,
        payload={"decision": "APPROVE"},
    )
    hints = [RetrievalHint(entry=fake_entry, score=0.9)]
    s = retrieval_service.format_hints_for_prompt(hints)
    assert s is not None
    parsed = json.loads(s)
    assert isinstance(parsed, list) and len(parsed) == 1
    assert parsed[0]["source_table"] == "ADJUDICATION"
    assert parsed[0]["score"] == 0.9


@pytest.mark.asyncio
async def test_retrieve_pattern_hint(
    retrieval_service: MemoryRetrievalService,
    memory_repository: MemoryRepository,
):
    # 聚类生成 pattern
    await _seed_judgments(memory_repository, n=3, base_source_id=5000, category_l1_id=7, area="Z")
    await memory_repository.cluster_pending(limit=10)
    hints = await retrieval_service.retrieve_pattern_hint("rust in area Z", top_k=3)
    assert len(hints) >= 1
    # pattern 形式的 hint：entry.payload 必有 skill_code / pattern_summary
    assert "skill_code" in hints[0].entry.payload


__all__ = []