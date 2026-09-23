"""CoverageRepository 单元测试：upsert / metric_key 唯一 / JSONB 互转 / 周期过滤 / 单查询 / 聚合。

依赖 conftest_coverage 的 sqlite 内存库 + session_factory 注入。
"""
from __future__ import annotations

import json

import pytest

from app.skill.coverage.domain.models import (
    CoverageSnapshot,
    build_metric_key,
    to_jsonb_safe,
)
from app.skill.coverage.infrastructure.repository import CoverageRepository

# ---------------------------------------------------------------- helpers --


def _snap(
    *,
    period_start: str = "2024-01-01T00:00:00+00:00",
    period_end: str = "2024-01-31T00:00:00+00:00",
    factory: str | None = "A",
    area: str | None = "B",
    category_l1_id: int | None = 1,
    issue_count: int = 5,
    closed_count: int = 3,
    overdue_count: int = 1,
    recurrence_count: int = 0,
    gap_severity: int = 0,
) -> CoverageSnapshot:
    rate = (closed_count / issue_count) if issue_count > 0 else 0.0
    return CoverageSnapshot(
        snapshot_id="snap-test-1",
        period_start=period_start,
        period_end=period_end,
        factory=factory,
        area=area,
        category_l1_id=category_l1_id,
        issue_count=issue_count,
        closed_count=closed_count,
        close_rate=rate,
        overdue_count=overdue_count,
        recurrence_count=recurrence_count,
        gap_severity=gap_severity,
    )


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_upsert_snapshot_inserts(coverage_engine, coverage_session_factory):
    repo = CoverageRepository(coverage_session_factory)
    snap = _snap()
    saved = await repo.upsert_snapshot(snap)
    assert saved.snapshot_id == "snap-test-1"
    assert saved.factory == "A"
    assert saved.issue_count == 5


@pytest.mark.asyncio
async def test_upsert_snapshot_idempotent_on_metric_key(coverage_engine, coverage_session_factory):
    """同周期 upsert → metric_key 唯一 → 命中后只更新 metric_value，不新增。"""
    repo = CoverageRepository(coverage_session_factory)
    snap1 = _snap(issue_count=5)
    await repo.upsert_snapshot(snap1)
    snap2 = _snap(issue_count=99, closed_count=42)
    saved = await repo.upsert_snapshot(snap2)
    assert saved.snapshot_id == "snap-test-1"
    assert saved.issue_count == 99
    assert saved.closed_count == 42
    # 整张表应当只有 1 条
    all_snaps = await repo.list_snapshots()
    assert len(all_snaps) == 1


@pytest.mark.asyncio
async def test_metric_key_collision_different_period(coverage_engine, coverage_session_factory):
    """不同周期 → metric_key 不同 → 共存。"""
    repo = CoverageRepository(coverage_session_factory)
    s1 = _snap(
        period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-31T00:00:00+00:00",
    )
    s2 = _snap(
        period_start="2024-02-01T00:00:00+00:00",
        period_end="2024-02-29T00:00:00+00:00",
    )
    await repo.upsert_snapshot(s1)
    await repo.upsert_snapshot(s2)
    rows = await repo.list_snapshots()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_jsonb_round_trip_via_metric_value(coverage_engine, coverage_session_factory):
    """upsert 写入的 CoverageSnapshot 可由 ``snapshot_from_metric_value`` 还原。"""

    repo = CoverageRepository(coverage_session_factory)
    original = _snap(issue_count=7, overdue_count=2, recurrence_count=3, gap_severity=1)
    await repo.upsert_snapshot(original)
    raw = await repo.fetch_by_period(original.period_start, original.period_end)
    assert raw is not None
    assert raw.snapshot_id == original.snapshot_id
    assert raw.issue_count == 7
    assert raw.overdue_count == 2
    assert raw.recurrence_count == 3
    assert raw.gap_severity == 1
    # 序列化 / 反序列化 JSON 字符串
    j = json.dumps(raw.to_dict())
    reloaded = CoverageSnapshot.from_dict(json.loads(j))
    assert reloaded == raw


@pytest.mark.asyncio
async def test_period_filter_on_list(coverage_engine, coverage_session_factory):
    """list_snapshots 支持周期过滤。"""
    repo = CoverageRepository(coverage_session_factory)
    s1 = _snap(
        period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-31T00:00:00+00:00",
    )
    s2 = _snap(
        period_start="2024-03-01T00:00:00+00:00",
        period_end="2024-03-31T00:00:00+00:00",
    )
    await repo.upsert_snapshot(s1)
    await repo.upsert_snapshot(s2)
    rows = await repo.list_snapshots(
        period_start="2024-02-15T00:00:00+00:00",
        period_end="2024-12-31T00:00:00+00:00",
    )
    assert len(rows) == 1
    assert rows[0].period_start.startswith("2024-03")


@pytest.mark.asyncio
async def test_fetch_by_period_returns_none_when_missing(coverage_engine, coverage_session_factory):
    repo = CoverageRepository(coverage_session_factory)
    out = await repo.fetch_by_period("2024-06-01T00:00:00+00:00", "2024-06-30T00:00:00+00:00")
    assert out is None


@pytest.mark.asyncio
async def test_aggregate_snapshots_computes_metrics(coverage_engine, coverage_session_factory):
    repo = CoverageRepository(coverage_session_factory)
    snaps = [
        _snap(issue_count=10, closed_count=5, overdue_count=1),
        _snap(issue_count=20, closed_count=15, overdue_count=3),
        _snap(issue_count=5, closed_count=5, overdue_count=0),
    ]
    agg = await repo.aggregate_snapshots(snaps)
    assert agg["total_snapshots"] == 3
    assert agg["total_issues"] == 35
    assert agg["total_overdue"] == 4
    # 平均 close_rate = ((5/10)+(15/20)+(5/5))/3
    assert agg["avg_close_rate"] == pytest.approx((0.5 + 0.75 + 1.0) / 3, abs=1e-3)
    # 排序后 [5,10,20] 中位数 10
    assert agg["p50_issue_count"] == 10


def test_build_metric_key_is_stable():
    assert build_metric_key("2024-01-01", "2024-01-31") == "coverage_analysis_2024-01-01_2024-01-31"
    # 时间格式里冒号 / 加号被规整化——colon 直接 strip、加号 → 'p'，避免 SQL 注入风险
    assert (
        build_metric_key("2024-01-01T00:00:00+00:00", "2024-01-31T00:00:00+00:00")
        == "coverage_analysis_2024-01-01T000000p0000_2024-01-31T000000p0000"
    )


def test_to_jsonb_safe_handles_dataclass_and_datetime():
    """CoverageSnapshot 落 JSONB 时 datetime/None/list 都能序列化。"""
    from datetime import UTC, datetime

    snap = CoverageSnapshot(
        snapshot_id="s1",
        period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-31T00:00:00+00:00",
        factory="A", area="B", category_l1_id=1,
    )
    d = to_jsonb_safe(snap)
    assert isinstance(d, dict)
    assert d["snapshot_id"] == "s1"
    assert d["factory"] == "A"

    # datetime 走 isoformat
    assert to_jsonb_safe(datetime(2024, 1, 1, tzinfo=UTC)) == "2024-01-01T00:00:00+00:00"
    # None / int / str 原样
    assert to_jsonb_safe(None) is None
    assert to_jsonb_safe(42) == 42
    assert to_jsonb_safe("x") == "x"
    # list 递归
    assert to_jsonb_safe([1, 2, 3]) == [1, 2, 3]