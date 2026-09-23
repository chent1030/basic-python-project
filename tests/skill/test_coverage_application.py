"""CoverageAnalysisService + CoverageIngestionService 测试。

四分析 / summary / 排序 / top_k / filter / 周期边界 / 异常指标 / record_snapshot。
"""
from __future__ import annotations

import pytest

from app.skill.coverage.application.services import (
    CoverageAnalysisService,
    CoverageIngestionService,
)
from app.skill.coverage.domain.models import CoverageSnapshot, SnapshotFilters
from tests.skill.coverage_helpers import FakeCoverageClient

PS = "2024-01-01T00:00:00+00:00"
PE = "2024-01-31T00:00:00+00:00"


def _r(**kw):
    """短写：dict 字段顺序自由，避免长行 E501。"""
    return kw


# ---------------------------------------------------------------- frequency ---


@pytest.mark.asyncio
async def test_analyze_frequency_aggregates_and_sorts():
    """两条同桶 (factory=A, area=B, cat=1) issue_count 应求和到 7+3=10。"""
    client = FakeCoverageClient(
        frequency=[
            _r(factory="A", area="B", category_l1_id=1,
               issue_count=7, closed_count=5, overdue_count=1, recurrence_count=0),
            _r(factory="A", area="B", category_l1_id=1,
               issue_count=3, closed_count=2, overdue_count=1, recurrence_count=1),
            _r(factory="A", area="B", category_l1_id=2,
               issue_count=1, closed_count=1, overdue_count=0, recurrence_count=0),
        ],
    )
    svc = CoverageAnalysisService(client)
    out = await svc.analyze_frequency(PS, PE, top_k=5)
    assert len(out) == 2
    top = out[0]
    assert top.factory == "A" and top.area == "B" and top.category_l1_id == 1
    assert top.issue_count == 10
    assert top.closed_count == 7
    assert top.overdue_count == 2
    assert top.close_rate == pytest.approx(0.7, abs=1e-3)


@pytest.mark.asyncio
async def test_analyze_frequency_respects_top_k():
    rows = [
        _r(factory=f"F{i}", area="B", category_l1_id=i,
           issue_count=10 - i, closed_count=i, overdue_count=0, recurrence_count=0)
        for i in range(10)
    ]
    client = FakeCoverageClient(frequency=rows)
    svc = CoverageAnalysisService(client)
    out = await svc.analyze_frequency(PS, PE, top_k=3)
    assert len(out) == 3
    # 第一个桶 issue_count 最大（F0 → 10）
    assert out[0].issue_count == 10
    # top_k 截断
    assert all(out[i].issue_count >= out[i + 1].issue_count for i in range(len(out) - 1))


@pytest.mark.asyncio
async def test_analyze_region_supervisor_sorts_by_overdue_then_open():
    client = FakeCoverageClient(
        region_supervisor=[
            _r(region="R1", supervisor_emp_no="S1",
               open_count=5, overdue_count=1, handled_count=10),
            _r(region="R1", supervisor_emp_no="S2",
               open_count=0, overdue_count=9, handled_count=1),
            _r(region="R2", supervisor_emp_no="S3",
               open_count=100, overdue_count=0, handled_count=0),
        ],
    )
    svc = CoverageAnalysisService(client)
    out = await svc.analyze_region_supervisor_load(PS, PE)
    assert out[0].supervisor_emp_no == "S2"  # overdue 9 排第一
    assert out[0].overdue_count == 9
    # 截 top 10：第 3 个 overdue=0，按 open_count desc
    assert out[-1].supervisor_emp_no == "S3"


@pytest.mark.asyncio
async def test_analyze_recurrence_filters_by_threshold():
    client = FakeCoverageClient(
        recurrence=[
            _r(issue_id=1, recurrence_count=5, last_recurrence_at="2024-01-10",
               factory="A", area="B", category_l1_id=1),
            _r(issue_id=2, recurrence_count=2, last_recurrence_at="2024-01-12",
               factory="A", area="B", category_l1_id=1),
            _r(issue_id=3, recurrence_count=1, last_recurrence_at="2024-01-14",
               factory="A", area="B", category_l1_id=1),
        ],
    )
    svc = CoverageAnalysisService(client)
    out = await svc.analyze_recurrence(PS, PE, threshold=2, top_k=10)
    assert len(out) == 2
    assert out[0].issue_id == 1  # recurrence 5
    # threshold=3 → 只 issue 1 命中
    out2 = await svc.analyze_recurrence(PS, PE, threshold=3, top_k=10)
    assert len(out2) == 1
    assert out2[0].issue_id == 1


@pytest.mark.asyncio
async def test_analyze_gaps_groups_by_storage_room_type():
    client = FakeCoverageClient(
        gaps=[
            _r(storage_room_type="cold", gap_severity=5, days_since_last_record=30,
               factory="A", area="B", category_l1_id=1),
            _r(storage_room_type="cold", gap_severity=3, days_since_last_record=10,
               factory="A", area="B", category_l1_id=2),
            _r(storage_room_type="raw", gap_severity=1, days_since_last_record=5,
               factory="A", area="B", category_l1_id=1),
        ],
    )
    svc = CoverageAnalysisService(client)
    out = await svc.analyze_gaps(PS, PE)
    # 应只剩 cold + raw
    srt_list = [g.storage_room_type for g in out]
    assert sorted(srt_list) == ["cold", "raw"]
    # cold max_severity=5, max_days=30
    cold = next(g for g in out if g.storage_room_type == "cold")
    assert cold.gap_severity == 5
    assert cold.days_since_last_record == 30


@pytest.mark.asyncio
async def test_summary_aggregates_metrics_and_anomalies():
    client = FakeCoverageClient(
        frequency=[
            _r(factory="A", area="B", category_l1_id=1,
               issue_count=10, closed_count=5, overdue_count=2, recurrence_count=1),
            _r(factory="A", area="B", category_l1_id=2,
               issue_count=4, closed_count=4, overdue_count=0, recurrence_count=0),
        ],
        region_supervisor=[
            _r(region="R1", supervisor_emp_no="S1",
               open_count=5, overdue_count=5, handled_count=0),
            _r(region="R1", supervisor_emp_no="S2",
               open_count=0, overdue_count=1, handled_count=5),
        ],
        recurrence=[
            _r(issue_id=1, recurrence_count=4, last_recurrence_at="2024-01-10",
               factory="A", area="B", category_l1_id=1),
            _r(issue_id=2, recurrence_count=2, last_recurrence_at="2024-01-12",
               factory="A", area="B", category_l1_id=1),
        ],
        gaps=[
            _r(storage_room_type="cold", gap_severity=3, days_since_last_record=10,
               factory="A", area="B", category_l1_id=1),
            _r(storage_room_type="raw", gap_severity=1, days_since_last_record=5,
               factory="A", area="B", category_l1_id=2),
        ],
    )
    svc = CoverageAnalysisService(client)
    summary = await svc.summary(PS, PE)
    # total_issues = 10 + 4 = 14
    assert summary.summary_metrics["total_issues"] == 14
    # total_gaps = 2（按 storage_room_type 聚合后）
    assert summary.summary_metrics["total_gaps"] == 2
    # avg_close_rate = ((5/10)+(4/4))/2 = 0.75
    assert summary.summary_metrics["avg_close_rate"] == pytest.approx(0.75, abs=1e-3)
    # p50_recur_count: 排序后 [2, 4] 中位 4
    assert summary.summary_metrics["p50_recur_count"] == 4
    # 异常
    assert len(summary.anomalies["top_3_high_freq"]) >= 1
    assert summary.anomalies["top_3_overdue_loading"][0]["supervisor_emp_no"] == "S1"
    assert summary.anomalies["gap_count_by_type"] == {"cold": 1, "raw": 1}


@pytest.mark.asyncio
async def test_summary_with_kinds_subset_only_runs_those():
    client = FakeCoverageClient(
        frequency=[_r(factory="A", area="B", category_l1_id=1,
                      issue_count=5, closed_count=5, overdue_count=0, recurrence_count=0)],
        gaps=[_r(storage_room_type="cold", gap_severity=1, days_since_last_record=5,
                 factory="A", area="B", category_l1_id=1)],
    )
    svc = CoverageAnalysisService(client)
    summary = await svc.summary(PS, PE, kinds=["frequency"])
    assert summary.frequency_trends  # 跑了
    assert summary.region_supervisor_loads == []
    assert summary.recurrences == []
    assert summary.gaps == []


@pytest.mark.asyncio
async def test_summary_rejects_unknown_kinds():
    client = FakeCoverageClient()
    svc = CoverageAnalysisService(client)
    with pytest.raises(ValueError, match="未知 kinds"):
        await svc.summary(PS, PE, kinds=["bogus"])


# ---------------------------------------------------------------- ingestion ---


@pytest.mark.asyncio
async def test_ingestion_record_snapshot_round_trips(coverage_engine, coverage_session_factory):
    """record_snapshot → 同周期再 record → upsert 不新增。"""
    from app.skill.coverage.infrastructure.repository import CoverageRepository
    repo = CoverageRepository(coverage_session_factory)
    svc = CoverageIngestionService(repo)
    snap = CoverageSnapshot(
        snapshot_id="snap-1",
        period_start=PS,
        period_end=PE,
        factory="A", area="B", category_l1_id=1,
        issue_count=10, closed_count=5, overdue_count=1,
    )
    saved = await svc.record_snapshot(snap)
    assert saved.snapshot_id == "snap-1"
    again = CoverageSnapshot(
        snapshot_id="snap-2",  # 不同的 id，但同 metric_key → upsert 命中
        period_start=PS,
        period_end=PE,
        factory="A", area="B", category_l1_id=2,
        issue_count=99, closed_count=80, overdue_count=5,
    )
    await svc.record_snapshot(again)
    # upsert 后 metric_value 里的 snapshot_id 不一定被更新——验证以 metric_key 唯一行为
    rows = await repo.list_snapshots()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_analyze_frequency_passes_filters_to_client():
    """filter (factory/area/category_l1_id) 应透传给 fetch_frequency。"""
    client = FakeCoverageClient(
        frequency=[_r(factory="A", area="B", category_l1_id=1,
                      issue_count=1, closed_count=1, overdue_count=0, recurrence_count=0)],
    )
    svc = CoverageAnalysisService(client)
    filters = SnapshotFilters(factory="A", area="B", category_l1_id=1)
    await svc.analyze_frequency(PS, PE, filters=filters)
    assert client.calls[0]["factory"] == "A"
    assert client.calls[0]["area"] == "B"
    assert client.calls[0]["category_l1_id"] == 1


@pytest.mark.asyncio
async def test_analyze_recurrence_passes_threshold_to_client():
    client = FakeCoverageClient(
        recurrence=[_r(issue_id=1, recurrence_count=5, last_recurrence_at="2024-01-10",
                       factory="A", area="B", category_l1_id=1)],
    )
    svc = CoverageAnalysisService(client)
    await svc.analyze_recurrence(PS, PE, threshold=4)
    assert client.calls[0]["threshold"] == 4


@pytest.mark.asyncio
async def test_summary_period_boundary_records_metrics_correctly(
    coverage_engine, coverage_session_factory,
):
    """period 边界只影响 metric_key；不同周期互不冲突。"""
    from app.skill.coverage.infrastructure.repository import CoverageRepository
    repo = CoverageRepository(coverage_session_factory)
    ing = CoverageIngestionService(repo)
    s1 = CoverageSnapshot(
        snapshot_id="s1", period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-31T00:00:00+00:00",
        factory="A", area="B", category_l1_id=1, issue_count=10,
    )
    s2 = CoverageSnapshot(
        snapshot_id="s2", period_start="2024-02-01T00:00:00+00:00",
        period_end="2024-02-29T00:00:00+00:00",
        factory="A", area="B", category_l1_id=1, issue_count=20,
    )
    await ing.record_snapshot(s1)
    await ing.record_snapshot(s2)
    rows = await repo.list_snapshots()
    assert len(rows) == 2