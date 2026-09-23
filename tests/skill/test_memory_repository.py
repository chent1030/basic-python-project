"""MemoryRepository 单元测试：upsert / 唯一冲突 / 软取代 / 向量检索 / 聚类。

依赖 conftest 注入 sqlite session_factory 与 EmbeddingClient。
"""
from __future__ import annotations

import pytest

from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import EMBEDDING_DIM, MemoryEntry
from app.skill.memory.infrastructure.repository import MemoryRepository


# ---------------------------------------------------------------- helpers --


def _entry(
    *,
    source_table: str = SourceTable.ADJUDICATION.value,
    source_id: int = 1,
    issue_id: int | None = 100,
    category_l1_id: int | None = 1,
    factory: str | None = "A",
    area: str | None = "B",
    severity: int | None = 3,
    embedding: list[float] | None = None,
    payload: dict | None = None,
    tags: list[str] | None = None,
) -> MemoryEntry:
    if embedding is None:
        embedding = [0.1] * EMBEDDING_DIM
    return MemoryEntry(
        source_table=source_table,
        source_id=source_id,
        issue_id=issue_id,
        category_l1_id=category_l1_id,
        factory=factory,
        area=area,
        severity=severity,
        embedding=embedding,
        payload=payload or {"decision": "APPROVE"},
        tags=tags or [],
    )


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_upsert_episode_inserts_and_returns(memory_repository: MemoryRepository):
    e = _entry(source_table=SourceTable.EVENT.value, source_id=10)
    saved = await memory_repository.upsert_episode(e)
    assert saved.id is not None and saved.id > 0
    assert saved.source_table == SourceTable.EVENT.value
    assert saved.is_active is True


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_unique_conflict(memory_repository: MemoryRepository):
    e1 = _entry(source_id=1)
    saved_a = await memory_repository.upsert_judgment(e1)
    # 重新 upsert 同 (source_table, source_id) → 同 id，payload 更新
    e2 = _entry(source_id=1, payload={"decision": "REJECT"})
    saved_b = await memory_repository.upsert_judgment(e2)
    assert saved_a.id == saved_b.id
    assert saved_b.payload == {"decision": "REJECT"}


@pytest.mark.asyncio
async def test_supersede_marks_old_inactive_and_links_new(memory_repository: MemoryRepository):
    old = await memory_repository.upsert_judgment(_entry(source_id=1, payload={"decision": "A"}))
    new = await memory_repository.upsert_judgment(_entry(source_id=2, payload={"decision": "B"}))
    ok = await memory_repository.supersede(old.id, new.id)
    assert ok is True
    listed = await memory_repository.list_by_issue(issue_id=100, include_inactive=True)
    by_id = {e.id: e for e in listed}
    assert by_id[old.id].is_active is False
    assert by_id[old.id].superseded_by == new.id
    assert by_id[new.id].is_active is True


@pytest.mark.asyncio
async def test_list_by_issue_filters(memory_repository: MemoryRepository):
    await memory_repository.upsert_episode(_entry(source_table=SourceTable.EVENT.value, source_id=1, issue_id=10))
    await memory_repository.upsert_episode(_entry(source_table=SourceTable.EVENT.value, source_id=2, issue_id=20))
    rows = await memory_repository.list_by_issue(issue_id=10)
    assert len(rows) == 1
    assert rows[0].source_id == 1


@pytest.mark.asyncio
async def test_search_by_embedding_cosine_top_k(memory_repository: MemoryRepository):
    # 三条；query 取第 1 条的方向，期望第 1 条排第一
    await memory_repository.upsert_judgment(
        _entry(source_id=1, embedding=[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2))
    )
    await memory_repository.upsert_judgment(
        _entry(source_id=2, embedding=[0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2))
    )
    await memory_repository.upsert_judgment(
        _entry(source_id=3, embedding=[-1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2))
    )
    query = [0.9, 0.1] + [0.0] * (EMBEDDING_DIM - 2)
    results = await memory_repository.search_by_embedding(query, top_k=2)
    assert results[0].source_id == 1
    assert results[0].score is not None and results[0].score > 0.9


@pytest.mark.asyncio
async def test_search_by_embedding_filters(memory_repository: MemoryRepository):
    await memory_repository.upsert_judgment(_entry(source_id=1, factory="A", area="X", category_l1_id=1))
    await memory_repository.upsert_judgment(_entry(source_id=2, factory="B", area="X", category_l1_id=1))
    await memory_repository.upsert_judgment(_entry(source_id=3, factory="A", area="Y", category_l1_id=1))
    query = [0.5] * EMBEDDING_DIM
    rows_a_x = await memory_repository.search_by_embedding(
        query, top_k=10, factory="A", area="X"
    )
    by_source = {r.source_id for r in rows_a_x}
    assert by_source == {1}


@pytest.mark.asyncio
async def test_embedding_string_round_trip():
    from app.skill.memory.domain.models import embedding_to_str, embedding_from_str

    # 必须严格 1024 维（合同：embedding_to_str/from_str 守维度，不让错位污染检索索引）
    v = [0.001 * i for i in range(EMBEDDING_DIM)]
    s = embedding_to_str(v)
    assert s.startswith("[") and s.endswith("]")
    v2 = embedding_from_str(s)
    assert v == v2
    # None / 空字符串 → None
    assert embedding_from_str(None) is None
    assert embedding_from_str("") is None
    # 维度错位 → ValueError
    with pytest.raises(ValueError):
        embedding_from_str("[0.1,0.2,0.3]")


@pytest.mark.asyncio
async def test_cluster_pending_creates_pattern_and_increments(memory_repository: MemoryRepository):
    for sid in (1, 2, 3):
        await memory_repository.upsert_judgment(
            _entry(source_id=sid, category_l1_id=5, area="B", factory="A")
        )
    patterns_first = await memory_repository.cluster_pending(limit=10)
    assert len(patterns_first) == 1
    p = patterns_first[0]
    assert p.example_count == 3
    assert "5" in (p.skill_code or "") or p.skill_code

    # 第二次聚合：再加 2 条同组 → example_count=5
    for sid in (4, 5):
        await memory_repository.upsert_judgment(
            _entry(source_id=sid, category_l1_id=5, area="B", factory="A")
        )
    patterns_second = await memory_repository.cluster_pending(limit=10)
    assert len(patterns_second) == 1
    assert patterns_second[0].id == p.id  # 同一 pattern
    assert patterns_second[0].example_count == 5


@pytest.mark.asyncio
async def test_search_patterns_by_embedding(memory_repository: MemoryRepository):
    for sid in (1, 2):
        await memory_repository.upsert_judgment(
            _entry(source_id=sid, category_l1_id=9, area="B", factory="A")
        )
    await memory_repository.cluster_pending(limit=10)
    query = [0.5] * EMBEDDING_DIM
    found = await memory_repository.search_patterns_by_embedding(query, top_k=3)
    assert len(found) >= 1
    assert found[0].skill_code


@pytest.mark.asyncio
async def test_unique_constraint_source_table_source_id(
    memory_repository: MemoryRepository,
    memory_session_factory,
):
    """UNIQUE(source_table, source_id) 守护：即使外部绕过 upsert 也应被约束。"""
    from app.skill.memory.infrastructure.repository import MemoryEntryORM

    e = _entry(source_id=99)
    await memory_repository.upsert_judgment(e)
    # 直接 INSERT 第二条 → 应抛 IntegrityError
    from sqlalchemy.exc import IntegrityError

    async with memory_session_factory() as session:
        session.add(
            MemoryEntryORM(
                source_table=e.source_table,
                source_id=e.source_id,
                payload={},
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            pytest.fail("UNIQUE 约束未生效，重复 source_id 应抛 IntegrityError")


@pytest.mark.asyncio
async def test_list_patterns_recent(memory_repository: MemoryRepository):
    await memory_repository.upsert_judgment(_entry(source_id=1, category_l1_id=1, area="X", factory="A"))
    await memory_repository.upsert_judgment(_entry(source_id=2, category_l1_id=2, area="Y", factory="B"))
    await memory_repository.cluster_pending(limit=10)
    patterns = await memory_repository.list_patterns(top_k=10)
    assert len(patterns) >= 2


__all__ = []