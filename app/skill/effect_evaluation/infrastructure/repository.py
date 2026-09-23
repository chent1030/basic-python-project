"""Effect evaluation 仓储层（FR-12）。

复用 :class:`MemoryMetricSnapshotORM` 表（波次 9 已落地）：

- ``metric_key='effect_evaluation_<start>_<end>_<scope>'`` 唯一键；
- ``metric_value`` 是 dict（JSONB），存四 metric + period_start/end/scope_key；
- record_snapshot = upsert；fetch_by_period / list_snapshots = read；
- PG 走 ``ON CONFLICT(metric_key) DO UPDATE``；SQLite 走 select-then-update 路径。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.skill.effect_evaluation.domain.models import (
    EffectComparison,
    EffectMetric,
    build_effect_metric_key,
    effect_metric_from_metric_value,
)
from app.skill.memory.infrastructure.repository import MemoryMetricSnapshotORM

logger = logging.getLogger(__name__)


class EffectMetricRepository:
    """Effect 仓储——upsert / list / fetch_by_period。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- write ----

    async def upsert_snapshot(
        self,
        period_start: str,
        period_end: str,
        scope_key: str,
        metric_value: dict[str, Any],
    ) -> EffectMetric:
        """Upsert 到 ``memory_metric_snapshot``（metric_key 唯一）。"""
        metric_key = build_effect_metric_key(period_start, period_end, scope_key)
        enriched = {
            **metric_value,
            "period_start": period_start,
            "period_end": period_end,
            "scope_key": scope_key,
            "kind": "effect_snapshot",
            "recorded_at": datetime.utcnow().isoformat(),
        }
        async with self._session_factory() as session:
            async with session.begin():
                bind = session.get_bind()
                dialect = getattr(bind, "dialect", None)
                is_pg = bool(dialect and dialect.name == "postgresql")
                if is_pg:
                    stmt = pg_insert(MemoryMetricSnapshotORM).values(
                        period_start=datetime.fromisoformat(period_start),
                        period_end=datetime.fromisoformat(period_end),
                        metric_key=metric_key,
                        metric_value=enriched,
                    ).on_conflict_do_update(
                        index_elements=["metric_key"],
                        set_={
                            "period_start": datetime.fromisoformat(period_start),
                            "period_end": datetime.fromisoformat(period_end),
                            "metric_value": enriched,
                        },
                    )
                    await session.execute(stmt)
                else:
                    existing = await self._get_by_metric_key(session, metric_key)
                    if existing is None:
                        existing = MemoryMetricSnapshotORM(
                            period_start=datetime.fromisoformat(period_start),
                            period_end=datetime.fromisoformat(period_end),
                            metric_key=metric_key,
                            metric_value=enriched,
                        )
                        session.add(existing)
                    else:
                        existing.period_start = datetime.fromisoformat(period_start)
                        existing.period_end = datetime.fromisoformat(period_end)
                        existing.metric_value = enriched
            await session.flush()
            row = await self._get_by_metric_key(session, metric_key)
            if row is None:
                raise RuntimeError("effect upsert 后未找到行")
            return effect_metric_from_metric_value(metric_key, dict(row.metric_value or {}))

    # -------------------------------------------------------------- read ----

    async def fetch_by_period(
        self,
        period_start: str,
        period_end: str,
        scope_key: str = "_",
    ) -> EffectMetric | None:
        metric_key = build_effect_metric_key(period_start, period_end, scope_key)
        async with self._session_factory() as session:
            row = await self._get_by_metric_key(session, metric_key)
            if row is None:
                return None
            return effect_metric_from_metric_value(metric_key, dict(row.metric_value or {}))

    async def list_snapshots(
        self,
        *,
        scope_key: str | None = None,
        limit: int = 50,
    ) -> list[EffectMetric]:
        """列出 effect snapshot；可选 scope 过滤（LIKE 前缀）。"""
        async with self._session_factory() as session:
            stmt = sa_select(MemoryMetricSnapshotORM).where(
                MemoryMetricSnapshotORM.metric_key.like("effect_evaluation_%"),
            )
            if scope_key is not None:
                stmt = stmt.where(
                    MemoryMetricSnapshotORM.metric_key.like(
                        f"effect_evaluation_%_{scope_key}",
                    ),
                )
            stmt = stmt.order_by(
                MemoryMetricSnapshotORM.recorded_at.desc(),
            ).limit(limit)
            result = await session.execute(stmt)
            return [
                effect_metric_from_metric_value(r.metric_key, dict(r.metric_value or {}))
                for r in result.scalars().all()
            ]

    async def _get_by_metric_key(
        self, session: AsyncSession, metric_key: str,
    ) -> MemoryMetricSnapshotORM | None:
        stmt = sa_select(MemoryMetricSnapshotORM).where(
            MemoryMetricSnapshotORM.metric_key == metric_key,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # -------------------------------------------------------------- pure compute ----

    @staticmethod
    def build_comparison(
        baseline: EffectMetric,
        current: EffectMetric,
    ) -> EffectComparison:
        """按 metric_value 字典求 delta_pct 并打 significance（见 application 层语义）。"""
        from app.skill.effect_evaluation.application.services import (
            EffectEvaluationService,
        )

        delta_pct = EffectEvaluationService.compute_delta_pct(baseline, current)
        return EffectComparison(
            baseline=baseline,
            current=current,
            delta_pct=delta_pct,
            significance="stable",  # 占位；具体由 application 二次打标
        )


__all__ = ["EffectMetricRepository"]