"""C2/C4 — 周报编排服务测试（StubDataFetcher + SQLite）。

覆盖：
- 正常编排：PENDING → RUNNING → ARCHIVING → COMPLETED；
- push_status = UNCONFIGURED 落库（AC-29）；
- 同窗口二次调用 → 跳过（幂等）；
- 归档 FAILED 路径（RustFS 不可达 → FAILED + error_code）；
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.projects.weekly_report.application.service import (
    WeeklyReportService,
    compute_window,
)
from app.projects.weekly_report.domain.models import (
    PUSH_STATUS_UNCONFIGURED,
    STATUS_COMPLETED,
    STATUS_FAILED,
)


@pytest.mark.asyncio
async def test_run_once_full_path_completes_and_marks_push_unconfigured(
    make_session_factory, make_service, fixed_now
):
    # 注入 fake uploader（直接 mock 成功 PUT，跳过真实 RustFS）
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("etag-abc", 4096))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    result = await svc.run_once(
        report_type="cps-issue-inspection", window=win
    )
    assert result["status"] == STATUS_COMPLETED
    assert result["push_status"] == PUSH_STATUS_UNCONFIGURED
    assert "weekly-reports/cps-issue-inspection/" in result["archive_object_key"]
    # 波次 7:COMPLETED 终态返回带 run_no(Java 首跑判定免查列表)
    assert result["run_no"] == 1

    # 持久化行落库
    from sqlalchemy import select

    from app.models.weekly_report import WeeklyReportRun

    async with make_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(WeeklyReportRun).where(
                        WeeklyReportRun.run_id == result["run_id"]
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.status == STATUS_COMPLETED
    assert row.push_status == PUSH_STATUS_UNCONFIGURED
    assert row.run_no == 1
    assert row.archive_object_key is not None
    assert row.archive_etag == "etag-abc"
    assert row.push_result is not None
    assert "推送未配置" in row.push_result


@pytest.mark.asyncio
async def test_run_once_idempotent_skips_when_already_completed(
    make_session_factory, make_service, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("etag-1", 4096))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    first = await svc.run_once(
        report_type="room-check", window=win
    )
    assert first["status"] == STATUS_COMPLETED

    second = await svc.run_once(report_type="room-check", window=win)
    # 同窗口第二次应跳过（latest_in_window == COMPLETED）
    assert second.get("skipped") is True
    assert "COMPLETED" in second.get("reason", "")
    # 波次 7:跳过返回也带 run_no（指向已存在的那次运行）
    assert second["run_no"] == first["run_no"]


@pytest.mark.asyncio
async def test_run_once_marks_failed_when_uploader_unavailable(
    make_session_factory, make_service, fixed_now
):
    """归档不可用 → FAILED，不伪造「已归档」语义。"""
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = False
    fake_uploader.unavailable_reason = "RustFS 周报配置不完整"

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    result = await svc.run_once(
        report_type="room-check", window=win
    )
    assert result["status"] == STATUS_FAILED
    assert "RustFS" in result["error"]
    assert result["run_no"] == 1  # 波次 7:FAILED 行已落库,返回带 run_no

    # 行落库为 FAILED + error_code = ARCHIVE_UNAVAILABLE
    from sqlalchemy import select

    from app.models.weekly_report import WeeklyReportRun

    async with make_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(WeeklyReportRun).where(
                        WeeklyReportRun.run_id == result["run_id"]
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].status == STATUS_FAILED
    assert rows[0].error_code == "ARCHIVE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_run_once_records_push_with_attempted_at(
    make_session_factory, make_service, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("e2", 2048))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    result = await svc.run_once(
        report_type="cps-issue-inspection", window=win
    )
    from sqlalchemy import select

    from app.models.weekly_report import WeeklyReportRun

    async with make_session_factory() as session:
        row = (
            await session.execute(
                select(WeeklyReportRun).where(
                    WeeklyReportRun.run_id == result["run_id"]
                )
            )
        ).scalar_one()
    # 「上传成功≠推送成功」（设计 §3.3 三态独立）
    assert row.status == STATUS_COMPLETED
    assert row.push_status == PUSH_STATUS_UNCONFIGURED
    assert row.push_attempted_at is not None
    assert row.push_attempts == 0  # NoOpPusher attempts = 0（未发起）


def test_compute_window_default_now():
    from zoneinfo import ZoneInfo

    win = compute_window()
    delta = win.window_end - win.window_start
    assert delta == timedelta(days=7)
    # 业务口径：Asia/Shanghai 周一 00:00（设计 §3.3）
    cn_start = win.window_start.astimezone(ZoneInfo("Asia/Shanghai"))
    cn_end = win.window_end.astimezone(ZoneInfo("Asia/Shanghai"))
    assert cn_start.isoweekday() == 1
    assert cn_end.isoweekday() == 1


@pytest.mark.asyncio
async def test_list_runs_returns_serialized_items(
    make_service, make_session_factory, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("e", 100))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    await svc.run_once(report_type="cps-issue-inspection", window=win)

    listed = await svc.list_runs(
        report_type="cps-issue-inspection", limit=10, offset=0
    )
    assert listed["total"] >= 1
    assert len(listed["items"]) >= 1
    assert listed["items"][0]["report_type"] == "cps-issue-inspection"
    assert listed["items"][0]["status"] == STATUS_COMPLETED


__all__ = ["WeeklyReportService"]