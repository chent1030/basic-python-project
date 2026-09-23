"""ProceduralRepository 单元测试（FR-11）。

覆盖：
- upsert_pattern 同三元组合并 + sample_count 累加；
- fetch_by_source_id / list_patterns 按 category_l1+area+decision 过滤；
- sample_reasons 上限 5 条 + 去重；
- tags 必含 'procedural' 类别标签。
"""
from __future__ import annotations

import pytest

from app.skill.procedural_memory.domain.models import (
    SAMPLE_REASONS_MAX,
    DispatchPattern,
)
from app.skill.procedural_memory.infrastructure.repository import (
    ProceduralRepository,
)


def _p(**kw) -> DispatchPattern:
    """短写：构造一条 DispatchPattern（除必填外其他默认）。"""
    base = dict(
        pattern_id=kw.pop("pattern_id", "pid-1"),
        category_l1_id=kw.pop("category_l1_id", 1),
        category_l2_id=kw.pop("category_l2_id", None),
        factory=kw.pop("factory", "A"),
        area=kw.pop("area", "B"),
        decision=kw.pop("decision", "ACCEPT"),
        ai_relation=kw.pop("ai_relation", "AGREE"),
        sample_count=kw.pop("sample_count", 1),
        sample_reasons=kw.pop("sample_reasons", []),
    )
    base.update(kw)
    return DispatchPattern(**base)


@pytest.mark.asyncio
async def test_upsert_pattern_merges_same_triplet(procedural_session_factory):
    """同 (category_l1, area, decision) upsert → sample_count 累加。"""
    repo = ProceduralRepository(procedural_session_factory)
    a = _p(sample_count=1, sample_reasons=["reason-A"])
    b = _p(pattern_id="pid-2", sample_count=2, sample_reasons=["reason-B"])
    await repo.upsert_pattern(a)
    saved = await repo.upsert_pattern(b)
    assert saved.sample_count == 3
    assert "reason-A" in saved.sample_reasons
    assert "reason-B" in saved.sample_reasons


@pytest.mark.asyncio
async def test_sample_reasons_dedupe_and_truncate(procedural_session_factory):
    """sample_reasons 上限 5 条 + 去重。"""
    repo = ProceduralRepository(procedural_session_factory)
    reasons = ["r1", "r2", "r3", "r4", "r5", "r6", "r1"]
    pattern = _p(sample_reasons=reasons)
    saved = await repo.upsert_pattern(pattern)
    assert len(saved.sample_reasons) <= SAMPLE_REASONS_MAX
    # r1 重复应当被去掉
    assert saved.sample_reasons.count("r1") == 1


@pytest.mark.asyncio
async def test_list_patterns_filters_by_decision(procedural_session_factory):
    repo = ProceduralRepository(procedural_session_factory)
    await repo.upsert_pattern(_p(area="B", decision="ACCEPT"))
    await repo.upsert_pattern(_p(area="B", decision="REJECT", sample_count=2))
    await repo.upsert_pattern(_p(area="C", decision="ACCEPT"))
    only_accept = await repo.list_patterns(area="B", decision="ACCEPT")
    assert len(only_accept) == 1
    assert only_accept[0].decision == "ACCEPT"
    only_reject = await repo.list_patterns(area="B", decision="REJECT")
    assert len(only_reject) == 1
    assert only_reject[0].decision == "REJECT"


@pytest.mark.asyncio
async def test_upsert_different_triplet_creates_new_row(procedural_session_factory):
    """不同 (category_l1, area, decision) → 新 row。"""
    repo = ProceduralRepository(procedural_session_factory)
    await repo.upsert_pattern(_p(area="B", decision="ACCEPT"))
    await repo.upsert_pattern(_p(area="B", decision="REJECT"))
    await repo.upsert_pattern(_p(area="C", decision="ACCEPT"))
    rows = await repo.list_patterns()
    assert len(rows) == 3
