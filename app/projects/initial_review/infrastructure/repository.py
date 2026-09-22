"""agent_initial_review_exec 仓储（SQLAlchemy async）。

事务口径：service 层负责事务边界（session.begin()），仓储只做查询/变更。
终态迁移一律带 ``status = 'RUNNING'`` 条件（乐观锁语义）：
- 防并发双执行双回调（第二个 UPDATE 影响 0 行即放弃）；
- 保证终态不可变（COMPLETED/FAILED 之后不再改写）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.agent_initial_review import AgentInitialReviewExec


class InitialReviewRepository:
    async def get_by_task_id(self, session, task_id: str) -> AgentInitialReviewExec | None:
        rows = await session.execute(
            select(AgentInitialReviewExec).where(AgentInitialReviewExec.task_id == task_id)
        )
        return rows.scalar_one_or_none()

    async def insert(self, session, row: AgentInitialReviewExec) -> AgentInitialReviewExec:
        """插入新执行记录；唯一键冲突（并发重放竞态）抛 IntegrityError 由上层重取。"""
        session.add(row)
        await session.flush()
        return row

    async def mark_terminal(
        self,
        session,
        task_id: str,
        *,
        status: str,
        overall: str | None = None,
        model_version: str | None = None,
        text_checks: dict | None = None,
        model_checks: dict | None = None,
        error: str | None = None,
        error_code: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        """RUNNING → 终态条件更新；返回是否真正生效（False = 已被并发方终结）。"""
        finished_at = finished_at or datetime.now(UTC)
        stmt = (
            update(AgentInitialReviewExec)
            .where(AgentInitialReviewExec.task_id == task_id)
            .where(AgentInitialReviewExec.status == "RUNNING")
            .values(
                status=status,
                overall=overall,
                model_version=model_version,
                text_checks=text_checks,
                model_checks=model_checks,
                error=error,
                error_code=error_code,
                finished_at=finished_at,
            )
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)

    async def sweep_expired(self, session, now: datetime | None = None) -> int:
        """惰性清理：RUNNING 且 deadline 已过的行 → FAILED/TIMEOUT（进程崩溃兜底）。"""
        now = now or datetime.now(UTC)
        stmt = (
            update(AgentInitialReviewExec)
            .where(AgentInitialReviewExec.status == "RUNNING")
            .where(AgentInitialReviewExec.deadline_at <= now)
            .values(
                status="FAILED",
                error_code="TIMEOUT",
                error="wall-clock deadline 超时（惰性扫描判定：进程可能在执行中崩溃）",
                finished_at=now,
            )
        )
        result = await session.execute(stmt)
        return int(result.rowcount or 0)

    async def record_callback(
        self,
        session,
        task_id: str,
        *,
        status: str,
        attempts: int,
        last_error: str | None,
    ) -> None:
        stmt = (
            update(AgentInitialReviewExec)
            .where(AgentInitialReviewExec.task_id == task_id)
            .values(
                callback_status=status,
                callback_attempts=attempts,
                callback_last_error=last_error,
            )
        )
        await session.execute(stmt)


__all__ = ["InitialReviewRepository", "IntegrityError"]
