"""EffectEvaluationService 应用层测试（FR-12）。

覆盖：
- record_current_snapshot 走 Java + 落 DB；
- compare_periods 跨周期对比 + significance 打标；
- detect_regression 阈值触发返回 metric_key 列表。
"""
from __future__ import annotations

import pytest

from app.skill.effect_evaluation.application.services import (
    EffectEvaluationService,
)
from app.skill.effect_evaluation.infrastructure.repository import EffectMetricRepository


class FakeEffectClient:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []
        self.calls: list[dict] = []

    async def iter_all_pages(self, start, end, *, size=100, **kwargs):
        self.calls.append({"start": start, "end": end, **kwargs})
        yield {"items": self._items, "total_pages": 1, "page": 1, "size": size}


PS1 = "2024-01-01T00:00:00+00:00"
PE1 = "2024-01-31T00:00:00+00:00"
PS2 = "2024-02-01T00:00:00+00:00"
PE2 = "2024-02-29T00:00:00+00:00"


def _metric_dict(**over) -> dict:
    base = {
        "aiPassRate": 0.85, "humanOverrideRate": 0.12,
        "recurrenceRate30d": 0.05, "coverageGapCount": 7,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_record_current_snapshot_upserts(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client = FakeEffectClient([_metric_dict()])
    svc = EffectEvaluationService(client=client, repository=repo)
    saved = await svc.record_current_snapshot(PS1, PE1, scope_key="_")
    assert saved.scope_key == "_"
    assert saved.period_start == PS1
    # DB 已落
    fetched = await repo.fetch_by_period(PS1, PE1, scope_key="_")
    assert fetched is not None


@pytest.mark.asyncio
async def test_record_current_snapshot_no_items_raises(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client = FakeEffectClient(items=[])
    svc = EffectEvaluationService(client=client, repository=repo)
    with pytest.raises(ValueError, match="无返回"):
        await svc.record_current_snapshot(PS1, PE1)


@pytest.mark.asyncio
async def test_compare_periods_returns_significance(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client = FakeEffectClient()
    svc = EffectEvaluationService(client=client, repository=repo)
    # baseline：ai_pass_rate=0.85, human_override_rate=0.10, recurrence=0.03
    await repo.upsert_snapshot(
        PS1, PE1, "_",
        {"ai_pass_rate": 0.85, "human_override_rate": 0.10,
         "recurrence_rate_30d": 0.03, "coverage_gap_count": 5},
    )
    # current: ai_pass_rate=0.60 (-29% regression); human_override_rate=0.25
    # (+150% regression); recurrence_rate_30d=0.025 (down -> improvement);
    # coverage_gap_count=8 (+60% regression)
    await repo.upsert_snapshot(
        PS2, PE2, "_",
        {"ai_pass_rate": 0.60, "human_override_rate": 0.25,
         "recurrence_rate_30d": 0.025, "coverage_gap_count": 8},
    )
    diffs = await svc.compare_periods(PS1, PE1, PS2, PE2, scope_key="_")
    by_key = {d.baseline.metric_key.replace("_baseline", ""): d.significance for d in diffs}
    assert by_key["ai_pass_rate"] == "regression"
    assert by_key["human_override_rate"] == "regression"
    assert by_key["coverage_gap_count"] == "regression"
    assert by_key["recurrence_rate_30d"] == "improvement"


@pytest.mark.asyncio
async def test_compare_periods_missing_baseline_raises(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client = FakeEffectClient()
    svc = EffectEvaluationService(client=client, repository=repo)
    with pytest.raises(ValueError, match="对比周期缺失"):
        await svc.compare_periods(PS1, PE1, PS2, PE2, scope_key="_")


@pytest.mark.asyncio
async def test_detect_regression_returns_metric_keys(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client = FakeEffectClient()
    svc = EffectEvaluationService(client=client, repository=repo)
    # baseline 三个月前（recorded_at 早）
    await repo.upsert_snapshot(
        "2023-11-01T00:00:00+00:00", "2023-11-30T00:00:00+00:00", "_",
        {"ai_pass_rate": 0.95, "human_override_rate": 0.05,
         "recurrence_rate_30d": 0.01, "coverage_gap_count": 2},
    )
    # current：本月——ai_pass_rate 掉到 0.50（regression）、human_override_rate 升到 0.20
    # （regression > 10%）、recurrence_rate_30d 升到 0.03（regression > 20%）
    await repo.upsert_snapshot(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", "_",
        {"ai_pass_rate": 0.50, "human_override_rate": 0.20,
         "recurrence_rate_30d": 0.03, "coverage_gap_count": 4},
    )
    reg = await svc.detect_regression(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00",
        scope_key="_", baseline_days=90,
    )
    assert "ai_pass_rate" in reg
    assert "human_override_rate" in reg
    assert "recurrence_rate_30d" in reg
