"""周报应用编排（C2 + C4 + C6 — §21 / 设计 §3.3 / §4）。

编排流水线：
1. 拿 PG advisory lock（同实例串行；多实例防双触发）；
2. 检查同 (type, window) 是否已 COMPLETED（已成功不入新 run）；
3. 否则 INSERT 新 run（PENDING）→ mark_running；
4. DataFetcher.fetch → Skill.render → RustFSWeeklyReportUploader.put_bytes
5. mark_archiving → mark_completed
6. Pusher.send → record_push（独立列，与 status 解耦）

事务边界：每步独立事务；失败时 mark_failed 写终态；步骤间不持锁靠
``status`` 条件 + (run_no 自增) + (type,window) UNIQUE 兜底幂等。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.weekly_report import WeeklyReportRun
from app.projects.weekly_report.domain.data_fetcher import (
    FetchRequest,
    WeeklyReportDataFetcher,
)
from app.projects.weekly_report.domain.models import (
    PUSH_STATUS_PENDING,
    PUSH_STATUS_UNCONFIGURED,
    ReportWindow,
)
from app.projects.weekly_report.infrastructure.config import WeeklyReportSettings
from app.projects.weekly_report.infrastructure.pusher import (
    WeeklyReportPusher,
    default_pusher,
)
from app.projects.weekly_report.infrastructure.repository import (
    WeeklyReportRepository,
    new_run_id,
)
from app.projects.weekly_report.infrastructure.rustfs_uploader import (
    RustFSWeeklyReportUploader,
)
from app.skills.weekly_report.skill import (
    RenderedReport,
    ReportSpec,
    WeeklyReportSkill,
    build_default_skill,
)

logger = logging.getLogger(__name__)

# PG advisory lock：单实例锁防双触发；sqlite 跳过（开发/测试靠 UNIQUE 兜底）
_ADVISORY_LOCK_KEY_WEEKLY = 0x57524550  # 'WREP'


def compute_window(now: datetime | None = None) -> ReportWindow:
    """上周一 00:00 → 本周一 00:00（Asia/Shanghai，左闭右开）。

    - ``now`` 不带 tz → 当作 Asia/Shanghai 解析；
    - ``now`` 带 tz → 转 Asia/Shanghai。
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    if now is None:
        now_local = datetime.now(tz)
    elif now.tzinfo is None:
        now_local = now.replace(tzinfo=tz)
    else:
        now_local = now.astimezone(tz)
    # ISO weekday: Mon=1..Sun=7
    days_since_monday = now_local.isoweekday() - 1
    this_monday_local = (
        now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=days_since_monday)
    )
    last_monday_local = this_monday_local - timedelta(days=7)
    return ReportWindow(
        window_start=last_monday_local.astimezone(UTC),
        window_end=this_monday_local.astimezone(UTC),
    )


class WeeklyReportService:
    """周报编排服务（构造时注入全部依赖，service 方法不解析配置）。"""

    def __init__(
        self,
        *,
        session_factory: Callable,
        data_fetcher: WeeklyReportDataFetcher,
        skill: WeeklyReportSkill | None = None,
        uploader: RustFSWeeklyReportUploader | None = None,
        pusher: WeeklyReportPusher | None = None,
        settings: WeeklyReportSettings,
        repository: WeeklyReportRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._data_fetcher = data_fetcher
        self._skill = skill or build_default_skill()
        self._uploader = uploader or RustFSWeeklyReportUploader(
            settings.rustfs
        )
        self._pusher = pusher or default_pusher()
        self._settings = settings
        self._repo = repository or WeeklyReportRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def settings(self) -> WeeklyReportSettings:
        return self._settings

    @property
    def skill(self) -> WeeklyReportSkill:
        return self._skill

    @property
    def uploader(self) -> RustFSWeeklyReportUploader:
        return self._uploader

    @property
    def pusher(self) -> WeeklyReportPusher:
        return self._pusher

    @property
    def repository(self) -> WeeklyReportRepository:
        return self._repo

    # ------------------------------------------------------------------ 编排
    async def run_once(
        self,
        *,
        report_type: str,
        window: ReportWindow | None = None,
    ) -> dict[str, Any]:
        """执行一次完整编排；返回结果摘要字典（不含敏感字段）。"""
        win = window or compute_window()
        async with self._session_factory() as session:
            # ---- 1) 单实例锁（PG advisory；sqlite 跳过）----
            dialect = session.bind.dialect.name if session.bind else ""
            locked = False
            if dialect == "postgresql":
                got = await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:k)"),
                    {"k": _ADVISORY_LOCK_KEY_WEEKLY},
                )
                locked = bool(got.scalar())
                if not locked:
                    return {
                        "skipped": True,
                        "reason": "已有周报编排正在执行（advisory lock 持有中）",
                        "report_type": report_type,
                        "window_start": win.window_start.isoformat(),
                        "window_end": win.window_end.isoformat(),
                    }
            # ---- 2) 同窗口已 COMPLETED 跳过 ----
            latest = await self._repo.latest_in_window(
                session,
                report_type=report_type,
                window_start=win.window_start,
                window_end=win.window_end,
            )
            if latest and latest.status == "COMPLETED":
                return {
                    "skipped": True,
                    "reason": "同窗口已 COMPLETED",
                    "run_id": latest.run_id,
                    "report_type": report_type,
                    "period": win.period_iso(),
                }
            # ---- 3) INSERT 新 run（PENDING）----
            runs_in_window = await self._repo.find_in_window(
                session,
                report_type=report_type,
                window_start=win.window_start,
                window_end=win.window_end,
            )
            next_run_no = (max((r.run_no for r in runs_in_window), default=0)) + 1
            row = WeeklyReportRun(
                run_id=new_run_id(),
                report_type=report_type,
                period=win.period_iso(),
                window_start=win.window_start,
                window_end=win.window_end,
                run_no=next_run_no,
                retry_count=0,
                status="PENDING",
                push_status=PUSH_STATUS_PENDING,
                created_at=self._clock(),
            )
            try:
                await self._repo.insert(session, row)
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return {
                    "skipped": True,
                    "reason": "并发同窗口已落库（UNIQUE 冲突兜底）",
                    "report_type": report_type,
                    "period": win.period_iso(),
                }
            run_id = row.run_id
            started = self._clock()

        # ---- 4) PENDING → RUNNING ----
        async with self._session_factory() as session:
            await self._repo.mark_running(
                session, run_id, started_at=started
            )
            await session.commit()

        # ---- 5) fetch + render ----
        snapshot = await self._data_fetcher.fetch(
            FetchRequest(report_type=report_type, window=win)
        )
        spec = ReportSpec(
            report_type=report_type,
            period=win.period_iso(),
            window_start=win.window_start,
            window_end=win.window_end,
            generated_at=self._clock(),
        )
        rendered: RenderedReport = self._skill.render(spec, snapshot)
        # Skill 返回 bytes → uploader 走 utf-8 编码

        # ---- 6) RUNNING → ARCHIVING（写入 model_version + snapshot）----
        async with self._session_factory() as session:
            await self._repo.mark_archiving(
                session,
                run_id,
                model_version=rendered.skill_version,
                snapshot=snapshot,
            )
            await session.commit()

        # ---- 7) 上传归档（§4 路径 weekly-reports/{type}/{yyyy-Ww}/{run_no}.html）----
        archive_bytes = rendered.body  # bytes 来自 Skill.render
        archive_object_key = (
            f"weekly-reports/{report_type}/{win.period_iso()}/{next_run_no}.html"
        )
        if not self._uploader.available:
            # 归档不可用 → 终态 FAILED（不伪造「已归档」）
            failed_at = self._clock()
            async with self._session_factory() as session:
                await self._repo.mark_failed(
                    session,
                    run_id,
                    error=self._uploader.unavailable_reason or "归档不可用",
                    error_code="ARCHIVE_UNAVAILABLE",
                    finished_at=failed_at,
                )
                await session.commit()
            return {
                "run_id": run_id,
                "report_type": report_type,
                "period": win.period_iso(),
                "status": "FAILED",
                "error": self._uploader.unavailable_reason,
            }
        try:
            etag, size = await self._uploader.put_bytes(
                object_key=archive_object_key,
                body=archive_bytes,
            )
        except Exception as exc:  # noqa: BLE001  # 转 FAILED
            failed_at = self._clock()
            async with self._session_factory() as session:
                await self._repo.mark_failed(
                    session,
                    run_id,
                    error=str(exc)[:500],
                    error_code="ARCHIVE_PUT_FAILED",
                    finished_at=failed_at,
                )
                await session.commit()
            logger.warning("weekly_report 归档失败 run_id=%s err=%s", run_id, exc)
            return {
                "run_id": run_id,
                "report_type": report_type,
                "period": win.period_iso(),
                "status": "FAILED",
                "error_code": "ARCHIVE_PUT_FAILED",
            }

        # ---- 8) ARCHIVING → COMPLETED ----
        finished_at = self._clock()
        async with self._session_factory() as session:
            await self._repo.mark_completed(
                session,
                run_id,
                object_key=archive_object_key,
                archive_bytes=int(size),
                archive_etag=etag,
                finished_at=finished_at,
            )
            await session.commit()

        # ---- 9) Pusher + record_push（独立列；不影响 status）----
        push_attempted_at = self._clock()
        push_payload: dict[str, Any] = {
            "run_id": run_id,
            "report_type": report_type,
            "period": win.period_iso(),
            "window_start": win.window_start.isoformat(),
            "window_end": win.window_end.isoformat(),
            "archive_object_key": archive_object_key,
            "archive_bytes": int(size),
            "template_id": rendered.template_id,
            "skill_version": rendered.skill_version,
        }
        try:
            from app.projects.weekly_report.domain.models import PushResult

            push_result_obj = await self._pusher.send(
                report_run_id=run_id, payload=push_payload
            )
        except Exception as exc:  # noqa: BLE001  # 推送异常 → FAILED 推送
            from app.projects.weekly_report.domain.models import PushResult

            push_result_obj = PushResult(
                ok=False,
                status="FAILED",
                detail=f"推送异常：{exc!s}"[:500],
                attempts=1,
            )
        async with self._session_factory() as session:
            await self._repo.record_push(
                session,
                run_id,
                push_status=push_result_obj.status,
                push_result=push_result_obj.detail,
                push_attempted_at=push_attempted_at,
                push_attempts=push_result_obj.attempts,
            )
            await session.commit()

        return {
            "run_id": run_id,
            "report_type": report_type,
            "period": win.period_iso(),
            "window_start": win.window_start.isoformat(),
            "window_end": win.window_end.isoformat(),
            "status": "COMPLETED",
            "archive_object_key": archive_object_key,
            "archive_bytes": int(size),
            "push_status": push_result_obj.status,
            "push_unavailable_reason": (
                self._settings.push_unavailable_reason
                if push_result_obj.status == PUSH_STATUS_UNCONFIGURED
                else None
            ),
        }

    # ------------------------------------------------------------------ 查询
    async def list_runs(
        self,
        *,
        report_type: str | None = None,
        period: str | None = None,
        status: str | None = None,
        push_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """C-05 列表查询：返回 ``items`` + ``total`` + ``push_unavailable_reason``。"""
        async with self._session_factory() as session:
            items, total = await self._repo.list_paginated(
                session,
                report_type=report_type,
                period=period,
                status=status,
                push_status=push_status,
                limit=limit,
                offset=offset,
            )
            rows = [
                {
                    "run_id": r.run_id,
                    "report_type": r.report_type,
                    "period": r.period,
                    "window_start": r.window_start.isoformat(),
                    "window_end": r.window_end.isoformat(),
                    "run_no": r.run_no,
                    "status": r.status,
                    "push_status": r.push_status,
                    "archive_object_key": r.archive_object_key,
                    "archive_bytes": r.archive_bytes,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "error_code": r.error_code,
                }
                for r in items
            ]
        return {
            "items": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "push_unavailable_reason": (
                self._settings.push_unavailable_reason
                if any(
                    (r.push_status == PUSH_STATUS_UNCONFIGURED)
                    for r in items
                )
                else None
            ),
        }

    async def get_run_for_download(
        self, run_id: str
    ) -> tuple[WeeklyReportRun, bytes] | None:
        """C-08：取 run + 归档字节；status != COMPLETED 返回 None。"""
        async with self._session_factory() as session:
            row = await self._repo.get_by_run_id(session, run_id)
        if row is None or row.status != "COMPLETED" or not row.archive_object_key:
            return None
        chunks: list[bytes] = []
        async for chunk in self._uploader.stream_get(row.archive_object_key):
            chunks.append(chunk)
        return row, b"".join(chunks)


def build_default_service(
    *,
    session_factory: Callable,
    data_fetcher: WeeklyReportDataFetcher | None = None,
    settings: WeeklyReportSettings,
) -> WeeklyReportService:
    """默认装配入口（生产 / 调度共用）。"""
    from app.projects.weekly_report.domain.data_fetcher import StubDataFetcher

    return WeeklyReportService(
        session_factory=session_factory,
        data_fetcher=data_fetcher or StubDataFetcher(),
        settings=settings,
    )


__all__ = [
    "WeeklyReportService",
    "build_default_service",
    "compute_window",
]


# 防止 zoneinfo 报警：保留对 timezone 模块的引用以便 ruff 不裁
_ = timezone  # noqa: F841
_ = json  # noqa: F841