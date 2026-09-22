"""weekly_report_run 仓储（SQLAlchemy async）。

事务口径（与 initial_review 对齐）：
- service 层负责事务边界（``session.begin()``），仓储只做查询 / 变更；
- 终态迁移一律带 ``status`` 条件（乐观锁语义）防并发双执行 + 终态不可变；
- ``(report_type, window_start, window_end)`` UNIQUE 约束兜底幂等键。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.weekly_report import WeeklyReportRun


def new_run_id() -> str:
    """公开 run_id（uuid4 hex，无连字符；URL 友好）。"""
    return uuid.uuid4().hex


def _period_to_window(period: str) -> tuple[datetime, datetime]:
    """``yyyy-Ww`` → (Monday 00:00 UTC, next Monday 00:00 UTC)。"""
    year_str, week_str = period.split("-W")
    year = int(year_str)
    week = int(week_str)
    monday = datetime.strptime(f"{year}-W{week:02d}-1", "%G-W%V-%u").replace(
        tzinfo=UTC
    )
    return monday, monday + timedelta(days=7)


class WeeklyReportRepository:
    async def get_by_run_id(
        self, session, run_id: str
    ) -> WeeklyReportRun | None:
        rows = await session.execute(
            select(WeeklyReportRun).where(WeeklyReportRun.run_id == run_id)
        )
        return rows.scalar_one_or_none()

    async def find_in_window(
        self,
        session,
        *,
        report_type: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[WeeklyReportRun]:
        """同 (type, window_start, window_end) 全部 run（按 run_no 升序）。"""
        rows = await session.execute(
            select(WeeklyReportRun)
            .where(WeeklyReportRun.report_type == report_type)
            .where(WeeklyReportRun.window_start == window_start)
            .where(WeeklyReportRun.window_end == window_end)
            .order_by(WeeklyReportRun.run_no.asc())
        )
        return list(rows.scalars())

    async def latest_in_window(
        self,
        session,
        *,
        report_type: str,
        window_start: datetime,
        window_end: datetime,
    ) -> WeeklyReportRun | None:
        runs = await self.find_in_window(
            session,
            report_type=report_type,
            window_start=window_start,
            window_end=window_end,
        )
        return runs[-1] if runs else None

    async def insert(
        self, session, row: WeeklyReportRun
    ) -> WeeklyReportRun:
        """INSERT 新运行行；UNIQUE 冲突抛 IntegrityError 由上层重取（幂等）。"""
        session.add(row)
        await session.flush()
        return row

    async def mark_running(
        self, session, run_id: str, *, started_at: datetime
    ) -> bool:
        stmt = (
            update(WeeklyReportRun)
            .where(WeeklyReportRun.run_id == run_id)
            .where(WeeklyReportRun.status == "PENDING")
            .values(status="RUNNING", started_at=started_at)
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def mark_archiving(
        self,
        session,
        run_id: str,
        *,
        model_version: str,
        snapshot: dict,
    ) -> bool:
        stmt = (
            update(WeeklyReportRun)
            .where(WeeklyReportRun.run_id == run_id)
            .where(WeeklyReportRun.status == "RUNNING")
            .values(
                status="ARCHIVING",
                model_version=model_version,
                report_snapshot=snapshot,
            )
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def mark_completed(
        self,
        session,
        run_id: str,
        *,
        object_key: str,
        archive_bytes: int,
        archive_etag: str | None,
        finished_at: datetime,
    ) -> bool:
        stmt = (
            update(WeeklyReportRun)
            .where(WeeklyReportRun.run_id == run_id)
            .where(WeeklyReportRun.status == "ARCHIVING")
            .values(
                status="COMPLETED",
                archive_object_key=object_key,
                archive_bytes=archive_bytes,
                archive_etag=archive_etag,
                finished_at=finished_at,
            )
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def mark_failed(
        self,
        session,
        run_id: str,
        *,
        error: str,
        error_code: str,
        finished_at: datetime,
    ) -> bool:
        stmt = (
            update(WeeklyReportRun)
            .where(WeeklyReportRun.run_id == run_id)
            .where(WeeklyReportRun.status.in_(("PENDING", "RUNNING", "ARCHIVING")))
            .values(
                status="FAILED",
                error=error,
                error_code=error_code,
                finished_at=finished_at,
            )
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def record_push(
        self,
        session,
        run_id: str,
        *,
        push_status: str,
        push_result: str | None,
        push_attempted_at: datetime,
        push_attempts: int,
    ) -> bool:
        stmt = (
            update(WeeklyReportRun)
            .where(WeeklyReportRun.run_id == run_id)
            .values(
                push_status=push_status,
                push_result=push_result,
                push_attempted_at=push_attempted_at,
                push_attempts=push_attempts,
            )
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def list_paginated(
        self,
        session,
        *,
        report_type: str | None = None,
        period: str | None = None,
        status: str | None = None,
        push_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WeeklyReportRun], int]:
        """分页列表 + 总数。

        period 形如 ``yyyy-Ww``，按 window_start 精确匹配（窗口与 ISO 周对齐）。
        """
        conditions = []
        if report_type:
            conditions.append(WeeklyReportRun.report_type == report_type)
        if status:
            conditions.append(WeeklyReportRun.status == status)
        if push_status:
            conditions.append(WeeklyReportRun.push_status == push_status)
        if period:
            monday, _ = _period_to_window(period)
            conditions.append(WeeklyReportRun.window_start == monday)

        where_clause = (
            select(WeeklyReportRun).where(*conditions)
            if conditions
            else select(WeeklyReportRun)
        )
        items_q = (
            where_clause.order_by(WeeklyReportRun.window_end.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await session.execute(items_q)).scalars())

        count_q = select(sa_func.count()).select_from(where_clause.subquery())
        total = int((await session.execute(count_q)).scalar_one() or 0)
        return items, total


__all__ = [
    "IntegrityError",
    "WeeklyReportRepository",
    "new_run_id",
]