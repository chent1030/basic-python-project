"""ProceduralMemoryService 应用层测试（FR-11）。

覆盖：
- retrieve_dispatch_patterns 三段加权（category_l1 / area / decision）；
- record_pattern 走 repository（采样合并）；
- consolidate_patterns 合并相同三元组；
- scoring 行为——部分段缺位按剩余段归一化。
"""
from __future__ import annotations

import pytest

from app.skill.procedural_memory.application.services import ProceduralMemoryService
from app.skill.procedural_memory.domain.models import (
    DispatchPattern,
    DispatchQuery,
)


class FakeProceduralClient:
    """模拟 Java dispatcher 端点。"""

    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.calls: list[dict] = []

    async def iter_all_pages(self, start, end, *, size=100, **kwargs):
        self.calls.append({"start": start, "end": end, **kwargs})
        yield {"items": self._items, "total_pages": 1, "page": 1, "size": size}


def _p(**kw) -> DispatchPattern:
    base = dict(
        pattern_id=kw.pop("pattern_id", "pid-1"),
        category_l1_id=kw.pop("category_l1_id", 1),
        category_l2_id=None,
        factory=kw.pop("factory", "A"),
        area=kw.pop("area", "B"),
        decision=kw.pop("decision", "ACCEPT"),
        ai_relation="AGREE",
        sample_count=kw.pop("sample_count", 1),
        sample_reasons=[],
    )
    base.update(kw)
    return DispatchPattern(**base)


@pytest.mark.asyncio
async def test_retrieve_three_segment_weighted_scoring():
    items = [
        {"category_l1_id": 1, "area": "B", "decision": "ACCEPT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": 3, "sample_reasons": []},
        {"category_l1_id": 1, "area": "B", "decision": "REJECT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": 1, "sample_reasons": []},
        {"category_l1_id": 2, "area": "B", "decision": "ACCEPT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": 5, "sample_reasons": []},
    ]
    client = FakeProceduralClient(items)
    svc = ProceduralMemoryService(client=client, repository=None)
    hints = await svc.retrieve_dispatch_patterns(
        DispatchQuery(category_l1_id=1, area="B", decision="ACCEPT"),
        top_k=3,
    )
    assert len(hints) == 3
    # 完美三段命中（cat1=1, area=B, decision=ACCEPT）= 1.0 排第一
    assert hints[0].scenario_similarity_score == pytest.approx(1.0)
    assert hints[0].pattern.decision == "ACCEPT"
    assert hints[0].pattern.area == "B"
    assert hints[0].pattern.category_l1_id == 1


@pytest.mark.asyncio
async def test_retrieve_partial_match_normalizes_remaining_segments():
    """缺 decision 段时，剩余 (category_l1, area) 满分 = 1.0。"""
    items = [
        {"category_l1_id": 1, "area": "B", "decision": "ACCEPT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": 1, "sample_reasons": []},
        {"category_l1_id": 1, "area": "C", "decision": "ACCEPT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": 1, "sample_reasons": []},
    ]
    svc = ProceduralMemoryService(
        client=FakeProceduralClient(items), repository=None,
    )
    hints = await svc.retrieve_dispatch_patterns(
        DispatchQuery(category_l1_id=1, area="B"),
        top_k=3,
    )
    # area=B 命中两段 (category_l1=1, area=B) → 1.0
    # area=C 仅 cat1=1 命中一段 → 1/2（剩余段归一化）= 0.5
    top = hints[0]
    assert top.pattern.area == "B"
    assert top.scenario_similarity_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_retrieve_top_k_truncates():
    items = [
        {"category_l1_id": 1, "area": "B", "decision": "ACCEPT", "ai_relation": "AGREE",
         "factory": "A", "sample_count": i, "sample_reasons": []}
        for i in range(7)
    ]
    svc = ProceduralMemoryService(
        client=FakeProceduralClient(items), repository=None,
    )
    hints = await svc.retrieve_dispatch_patterns(
        DispatchQuery(category_l1_id=1, area="B", decision="ACCEPT"),
        top_k=3,
    )
    assert len(hints) == 3


@pytest.mark.asyncio
async def test_record_pattern_calls_repository_upsert(procedural_session_factory):
    """service.record_pattern → repository.upsert_pattern（合并累计）。"""
    from app.skill.procedural_memory.infrastructure.repository import (
        ProceduralRepository,
    )

    repo = ProceduralRepository(procedural_session_factory)
    svc = ProceduralMemoryService(
        client=FakeProceduralClient([]), repository=repo,
    )
    await svc.record_pattern(_p(sample_count=2, sample_reasons=["r1"]))
    saved = await svc.record_pattern(_p(sample_count=1, sample_reasons=["r2"]))
    assert saved.sample_count == 3
    assert "r1" in saved.sample_reasons and "r2" in saved.sample_reasons


@pytest.mark.asyncio
async def test_consolidate_patterns_merges_same_triplet(procedural_session_factory):
    from app.skill.procedural_memory.infrastructure.repository import (
        ProceduralRepository,
    )

    repo = ProceduralRepository(procedural_session_factory)
    # 模拟「同三元组但 source_id 不同」的并发遗留场景：手工插入两条 row 共享
    # ``(category_l1, area, decision)`` 但 source_id 不同——这是合并算法的目标场景。
    from app.skill.procedural_memory.infrastructure.repository import (
        PROCEDURAL_SOURCE_TABLE,
        DispatchPatternORM,
        _build_payload,
        _source_id_for,
    )
    factory = procedural_session_factory
    async with factory() as session:
        async with session.begin():
            for pid, cnt, reason in [("pid-A", 3, "from-a"), ("pid-B", 5, "from-b")]:
                pattern = _p(pattern_id=pid, area="B", decision="ACCEPT",
                             sample_count=cnt, sample_reasons=[reason])
                source_id = _source_id_for(pattern) + (hash(pid) % 1000)
                row = DispatchPatternORM(
                    source_table=PROCEDURAL_SOURCE_TABLE,
                    source_id=source_id,
                    category_l1_id=pattern.category_l1_id,
                    category_l2_id=pattern.category_l2_id,
                    factory=pattern.factory,
                    area=pattern.area,
                    payload=_build_payload(
                        pattern, sample_count=pattern.sample_count,
                        reasons=pattern.sample_reasons,
                    ),
                    tags=["procedural:dispatch_pattern", "procedural",
                          f"procedural:{source_id}"],
                    is_active=True,
                )
                session.add(row)
    svc = ProceduralMemoryService(
        client=FakeProceduralClient([]), repository=repo,
    )
    summary = await svc.consolidate_patterns(since_days=30)
    assert summary["consolidated_groups"] >= 1
    assert summary["merged_count"] >= 2
    # 合并产物（reset_pattern 用 _source_id_for 算出的稳定 source_id）sample_count 应为 sum
    rows = await repo.list_patterns(area="B", decision="ACCEPT")
    counts = [r.sample_count for r in rows]
    assert 8 in counts, f"expected sample_count=8 row, got {counts}"
    merged = next(r for r in rows if r.sample_count == 8)
    assert set(merged.sample_reasons) == {"from-a", "from-b"}
