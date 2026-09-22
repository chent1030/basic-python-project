"""C3 — 周报定时任务。

调度口径（设计 §3.3 / AC-28）：
- cron = ``0 8 * * MON``，timezone = ``Asia/Shanghai``；
- 窗口 [last Mon 00:00, this Mon 00:00) 含头不含尾；
- misfire_grace_time = 7200s（2h 兜底）；
- ``max_instances = 1`` 防止任务重入（单机）。

本任务不做环境变量短路（确保 @scheduled 被注册，调度器 lifespan 启动），
真正的运行开关在 service.run_once 内的 ``pg_try_advisory_xact_lock`` 与
``(type, window) UNIQUE`` 兜底；额外通过
``CPS_WEEKLY_REPORT_SCHED_ENABLED=false``（开发机默认）控制 ``sched_enabled``
—— 这只是把 ``report_types`` 收敛到空列表，让默认装配跳过遍历。
"""
from __future__ import annotations

import logging

from app.core.scheduler import scheduled
from app.projects.weekly_report.application.service import (
    WeeklyReportService,
    build_default_service,
    compute_window,
)
from app.projects.weekly_report.domain.data_fetcher import StubDataFetcher
from app.projects.weekly_report.infrastructure.config import (
    load_weekly_report_settings,
)

log = logging.getLogger("app.tasks.weekly_report")


def _build_task_service() -> WeeklyReportService | None:
    """lazy-import 数据源 → 服务实例；返回 None 表示数据源未配置（开发机）。"""
    from app.core.datasource import DatasourceManager, datasources

    cfg = load_weekly_report_settings()
    if not cfg.report_types:
        return None
    manager: DatasourceManager = datasources
    if "postgres_primary" not in manager.names():
        return None
    session_factory = manager.get_session_factory("postgres_primary")
    return build_default_service(
        session_factory=session_factory,
        data_fetcher=StubDataFetcher(),
        settings=cfg,
    )


@scheduled(
    cron="0 8 * * MON",
    misfire_grace_time=7200,
    max_instances=1,
    coalesce=True,
)
async def weekly_report_run() -> None:
    """每周一 08:00 Asia/Shanghai 触发，遍历 report_types 各跑一次。"""
    service = _build_task_service()
    if service is None:
        log.info(
            "[weekly_report_run] skip (no report_types 或数据源未配置)"
        )
        return
    window = compute_window()
    for report_type in service.settings.report_types:
        try:
            result = await service.run_once(
                report_type=report_type, window=window
            )
            log.info(
                "[weekly_report_run] report_type=%s status=%s run_id=%s",
                report_type,
                result.get("status"),
                result.get("run_id"),
            )
        except Exception as exc:  # noqa: BLE001  # 不让任务整体挂掉
            log.exception(
                "[weekly_report_run] report_type=%s 失败：%s",
                report_type,
                exc,
            )


__all__ = ["weekly_report_run"]