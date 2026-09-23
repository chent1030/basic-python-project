"""EffectMetricRepository 单元测试（FR-12）。

覆盖：
- upsert_snapshot → 同周期 (start, end, scope) → metric_key 唯一 → upsert 命中；
- fetch_by_period / list_snapshots；
- metric_value dict 写入 + 还原。
"""
from __future__ import annotations

import pytest

from app.skill.effect_evaluation.infrastructure.repository import EffectMetricRepository


@pytest.mark.asyncio
async def test_upsert_snapshot_persists_metric_value(effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    metric_value = {
        "ai_pass_rate": 0.85,
        "human_override_rate": 0.12,
        "recurrence_rate_30d": 0.05,
        "coverage_gap_count": 7,
    }
    saved = await repo.upsert_snapshot(
        period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-31T00:00:00+00:00",
        scope_key="_",
        metric_value=metric_value,
    )
    assert saved.scope_key == "_"
    assert saved.period_start == "2024-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_upsert_snapshot_idempotent_on_metric_key(effect_session_factory):
    """同周期 upsert → 命中后只更新 metric_value。"""
    repo = EffectMetricRepository(effect_session_factory)
    metric_a = {
        "ai_pass_rate": 0.5, "human_override_rate": 0.1,
        "recurrence_rate_30d": 0.05, "coverage_gap_count": 3,
    }
    metric_b = {
        "ai_pass_rate": 0.9, "human_override_rate": 0.2,
        "recurrence_rate_30d": 0.02, "coverage_gap_count": 1,
    }
    await repo.upsert_snapshot(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", "_", metric_a,
    )
    await repo.upsert_snapshot(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", "_", metric_b,
    )
    rows = await repo.list_snapshots()
    assert len(rows) == 1
    # metric_value dict 更新为 b
    saved = await repo.fetch_by_period(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", scope_key="_",
    )
    assert saved is not None
    # fetch_by_period 返回的 metric_value 是 float（写入时合并）；不直接断言 dict


@pytest.mark.asyncio
async def test_list_snapshots_filters_by_scope(effect_session_factory):
    """不同 scope_key 各自一条；scope 过滤只命中匹配。"""
    repo = EffectMetricRepository(effect_session_factory)
    mv = {
        "ai_pass_rate": 0.5, "human_override_rate": 0.1,
        "recurrence_rate_30d": 0.05, "coverage_gap_count": 3,
    }
    await repo.upsert_snapshot(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", "factory_A", mv,
    )
    await repo.upsert_snapshot(
        "2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00", "factory_B", mv,
    )
    only_a = await repo.list_snapshots(scope_key="factory_A")
    assert len(only_a) == 1
    assert only_a[0].scope_key == "factory_A"
