"""Coverage 仓储层：upsert_snapshot / list_snapshots / fetch_by_period / aggregate。

要点：
- 复用 :class:`MemoryMetricSnapshotORM` 表（已在波次 9 落地）；
  ``metric_key='coverage_analysis_<period_start>_<period_end>'`` 唯一键 → 同周期幂等；
- 走 :class:`MemoryRepository` 的 session_factory 模式——不引入新 schema，避免重复造表；
- 支持纯计算模式（不调 ``record_snapshot`` 时仅返回 dataclass，不落 DB）；
- 写入路径：PG 走 ``ON CONFLICT(metric_key) DO UPDATE``；SQLite 走 select-then-update
  的 Python 等价路径（同 MemoryRepository 模式）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.skill.coverage.domain.models import (
    CoverageSnapshot,
    build_metric_key,
    snapshot_from_metric_value,
    to_jsonb_safe,
)
from app.skill.memory.infrastructure.repository import MemoryMetricSnapshotORM

logger = logging.getLogger(__name__)


class CoverageRepository:
    """Coverage 仓储：纯计算（不入库） 或 落 ``memory_metric_snapshot``。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- pure compute ----

    def build_snapshot(
        self,
        *,
        period_start: str,
        period_end: str,
        factory: str | None,
        area: str | None,
        category_l1_id: int | None,
        raw: dict[str, Any],
    ) -> CoverageSnapshot:
        """从 Java 端 frequency 一行 dict → CoverageSnapshot（不入库）。"""
        merged = dict(raw)
        merged.setdefault("period_start", period_start)
        merged.setdefault("period_end", period_end)
        merged.setdefault("factory", factory)
        merged.setdefault("area", area)
        merged.setdefault("category_l1_id", category_l1_id)
        if not merged.get("snapshot_id"):
            import uuid as _uuid

            merged["snapshot_id"] = str(_uuid.uuid4())
        return CoverageSnapshot.from_dict(merged)

    # -------------------------------------------------------------- write ----

    async def upsert_snapshot(self, snapshot: CoverageSnapshot) -> CoverageSnapshot:
        """upsert 到 ``memory_metric_snapshot``（metric_key = coverage_analysis_<period>）。

        返回持久化后的 CoverageSnapshot（recorded_at 同步为 DB 默认时间）。
        """
        metric_key = build_metric_key(snapshot.period_start, snapshot.period_end)
        metric_value = {
            "snapshot": snapshot.to_dict(),
            "kind": "coverage_snapshot",
        }
        metric_value_safe = to_jsonb_safe(metric_value)
        async with self._session_factory() as session:
            async with session.begin():
                bind = session.get_bind()
                dialect = getattr(bind, "dialect", None)
                is_pg = bool(dialect and dialect.name == "postgresql")
                if is_pg:
                    stmt = pg_insert(MemoryMetricSnapshotORM).values(
                        period_start=datetime.fromisoformat(snapshot.period_start),
                        period_end=datetime.fromisoformat(snapshot.period_end),
                        metric_key=metric_key,
                        metric_value=metric_value_safe,
                    ).on_conflict_do_update(
                        index_elements=["metric_key"],
                        set_={
                            "period_start": datetime.fromisoformat(snapshot.period_start),
                            "period_end": datetime.fromisoformat(snapshot.period_end),
                            "metric_value": metric_value_safe,
                        },
                    )
                    await session.execute(stmt)
                else:
                    existing = await self._get_by_metric_key(session, metric_key)
                    if existing is None:
                        existing = MemoryMetricSnapshotORM(
                            period_start=datetime.fromisoformat(snapshot.period_start),
                            period_end=datetime.fromisoformat(snapshot.period_end),
                            metric_key=metric_key,
                            metric_value=metric_value_safe,
                        )
                        session.add(existing)
                    else:
                        existing.period_start = datetime.fromisoformat(snapshot.period_start)
                        existing.period_end = datetime.fromisoformat(snapshot.period_end)
                        existing.metric_value = metric_value_safe
            await session.flush()
            row = await self._get_by_metric_key(session, metric_key)
            if row is None:
                raise RuntimeError("upsert 后未找到行——DB 状态异常")
            return snapshot_from_metric_value(dict(row.metric_value or {}))

    async def _get_by_metric_key(
        self, session: AsyncSession, metric_key: str
    ) -> MemoryMetricSnapshotORM | None:
        stmt = sa_select(MemoryMetricSnapshotORM).where(
            MemoryMetricSnapshotORM.metric_key == metric_key,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # -------------------------------------------------------------- read ----

    async def fetch_by_period(
        self,
        period_start: str,
        period_end: str,
    ) -> CoverageSnapshot | None:
        """按周期取唯一快照（period 决定唯一）。"""
        async with self._session_factory() as session:
            row = await self._get_by_metric_key(
                session, build_metric_key(period_start, period_end),
            )
            if row is None:
                return None
            return snapshot_from_metric_value(dict(row.metric_value or {}))

    async def list_snapshots(
        self,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        limit: int = 50,
    ) -> list[CoverageSnapshot]:
        """列出最近 N 条快照；可选周期过滤。"""
        async with self._session_factory() as session:
            stmt = sa_select(MemoryMetricSnapshotORM).where(
                MemoryMetricSnapshotORM.metric_key.like("coverage_analysis_%"),
            )
            if period_start is not None:
                stmt = stmt.where(
                    MemoryMetricSnapshotORM.period_start
                    >= datetime.fromisoformat(period_start),
                )
            if period_end is not None:
                stmt = stmt.where(
                    MemoryMetricSnapshotORM.period_end
                    <= datetime.fromisoformat(period_end),
                )
            stmt = stmt.order_by(
                MemoryMetricSnapshotORM.recorded_at.desc(),
            ).limit(limit)
            result = await session.execute(stmt)
            return [
                snapshot_from_metric_value(dict(r.metric_value or {}))
                for r in result.scalars().all()
            ]

    # -------------------------------------------------------------- aggregate ----

    async def aggregate_snapshots(
        self,
        snapshots: list[CoverageSnapshot],
    ) -> dict[str, Any]:
        """对内存中的快照集合做聚合（不进 DB，跨 dialect 一致）。"""
        if not snapshots:
            return {
                "total_snapshots": 0,
                "total_issues": 0,
                "total_overdue": 0,
                "avg_close_rate": 0.0,
                "p50_issue_count": 0,
            }
        total = len(snapshots)
        total_issues = sum(s.issue_count for s in snapshots)
        total_overdue = sum(s.overdue_count for s in snapshots)
        rates = sorted(s.close_rate for s in snapshots)
        avg_close_rate = sum(rates) / len(rates) if rates else 0.0
        counts = sorted(s.issue_count for s in snapshots)
        p50 = counts[len(counts) // 2] if counts else 0
        return {
            "total_snapshots": total,
            "total_issues": total_issues,
            "total_overdue": total_overdue,
            "avg_close_rate": round(avg_close_rate, 4),
            "p50_issue_count": p50,
        }


__all__ = ["CoverageRepository"]