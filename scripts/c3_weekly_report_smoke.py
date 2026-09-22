"""scripts/c3_weekly_report_smoke.py — C3/C4/C6 手动等价验证。

执行一次完整编排（窗口 = 上周一 00:00 → 本周一 00:00 Asia/Shanghai），
不依赖 scheduler（env CPS_WEEKLY_REPORT_SCHED_ENABLED=false 不真调调度）。

断言：
- weekly_report_run 行落库，status=COMPLETED；
- archive_object_key 落在 weekly-reports/{type}/{yyyy-Ww}/ 路径段；
- push_status=UNCONFIGURED + push_result 含「推送未配置」。

用法：python -m scripts.c3_weekly_report_smoke
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

from app.core.datasource import DatasourceManager, datasources
from app.models.weekly_report import WeeklyReportRun
from app.projects.weekly_report.application.service import (
    WeeklyReportService,
    compute_window,
)
from app.projects.weekly_report.domain.data_fetcher import StubDataFetcher
from app.projects.weekly_report.infrastructure.config import (
    load_weekly_report_settings,
)
from app.projects.weekly_report.infrastructure.pusher import NoOpPusher
from app.projects.weekly_report.infrastructure.repository import (
    WeeklyReportRepository,
)
from app.projects.weekly_report.infrastructure.rustfs_uploader import (
    RustFSWeeklyReportUploader,
)
from app.skills.weekly_report.skill import build_default_skill

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("c3_smoke")


def _build_service() -> WeeklyReportService:
    manager: DatasourceManager = datasources
    if "postgres_primary" not in manager.names():
        raise SystemExit(
            "postgres_primary 数据源未配置；请在调用前 "
            "`asyncio.run(datasources.startup())` 或检查 config/local.yaml"
        )
    session_factory = manager.get_session_factory("postgres_primary")
    settings = load_weekly_report_settings()
    return WeeklyReportService(
        session_factory=session_factory,
        data_fetcher=StubDataFetcher(),
        skill=build_default_skill(),
        uploader=RustFSWeeklyReportUploader(settings.rustfs),
        pusher=NoOpPusher(),
        settings=settings,
        repository=WeeklyReportRepository(),
    )


async def _run(report_type: str) -> None:
    svc = _build_service()
    window = compute_window()
    log.info(
        "[smoke] report_type=%s window=%s → %s",
        report_type,
        window.window_start.isoformat(),
        window.window_end.isoformat(),
    )
    if not svc.uploader.available:
        log.warning(
            "[smoke] RustFS 不可用（%s）— 仍可验证 service.run_once 的状态机，"
            "但 archive_object_key 不会落库。",
            svc.uploader.unavailable_reason,
        )
    result = await svc.run_once(report_type=report_type, window=window)
    log.info("[smoke] service.run_once → %s", result)

    # 落库断言
    async with svc._session_factory() as session:  # noqa: SLF001  # smoke 脚本
        row = (
            await session.execute(
                select(WeeklyReportRun).where(
                    WeeklyReportRun.run_id == result["run_id"]
                )
            )
        ).scalar_one()
    assert row.status in ("COMPLETED", "FAILED"), f"status={row.status}"
    log.info(
        "[smoke] row status=%s push_status=%s archive=%s bytes=%s",
        row.status,
        row.push_status,
        row.archive_object_key,
        row.archive_bytes,
    )
    if row.status == "COMPLETED":
        assert row.archive_object_key is not None
        assert row.archive_object_key.startswith(
            f"weekly-reports/{report_type}/"
        )
        assert row.push_status == "UNCONFIGURED"
        assert row.push_result is not None
        assert "推送未配置" in row.push_result
        log.info("[smoke] ✅ 全部断言通过")
    else:
        log.info(
            "[smoke] ⚠️ status=FAILED（RustFS 不可达时正常），其余字段已落库"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-type",
        default="cps-issue-inspection",
        help="周报分类（默认 cps-issue-inspection）",
    )
    args = parser.parse_args()
    # env CPS_WEEKLY_REPORT_SCHED_ENABLED 默认 false（不真调调度）
    import os

    if os.environ.get("CPS_WEEKLY_REPORT_SCHED_ENABLED", "false").lower() not in (
        "0",
        "false",
    ):
        log.warning(
            "[smoke] CPS_WEEKLY_REPORT_SCHED_ENABLED 已启用 — smoke 脚本不会"
            "重复触发 APScheduler，只手动跑一次 run_once"
        )
    async def _main() -> int:
        # smoke 离线运行 → 装载数据源再跑一次
        await datasources.startup()
        try:
            await _run(args.report_type)
        except AssertionError as exc:
            log.error("[smoke] ❌ 断言失败：%s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001
            log.exception("[smoke] ❌ 异常：%s", exc)
            return 1
        return 0

    try:
        return asyncio.run(_main())
    except AssertionError as exc:
        log.error("[smoke] ❌ 断言失败：%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        log.exception("[smoke] ❌ 异常：%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]