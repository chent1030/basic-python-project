"""Effect evaluation 应用层（FR-12）。

核心方法：

- ``record_current_snapshot`` 拉 Java effect → upsert 到
  ``memory_metric_snapshot``（metric_key 唯一）；
- ``compare_periods`` 读 baseline + current → 按 metric_value 字典 diff →
  ``list[EffectComparison]``；
- ``detect_regression`` 拉 current → 与同 scope 历史 baseline 比对 → 标记
  ``ai_pass_rate`` 下降 >5% / ``human_override_rate`` 上升 >10% /
  ``recurrence_rate_30d`` 上升 >20% 的 metric_key 列表。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.skill.effect_evaluation.domain.models import (
    EFFECT_METRIC_KEYS,
    METRIC_AI_PASS_RATE,
    METRIC_COVERAGE_GAP_COUNT,
    METRIC_HUMAN_OVERRIDE_RATE,
    METRIC_RECURRENCE_RATE_30D,
    EffectComparison,
    EffectMetric,
    build_effect_metric_key,
)
from app.skill.effect_evaluation.infrastructure.java_client import EffectMetricClient
from app.skill.effect_evaluation.infrastructure.repository import EffectMetricRepository

logger = logging.getLogger(__name__)


# 回归阈值（绝对百分比变化）。
THRESHOLD_AI_PASS_DROP_PCT: float = 5.0
THRESHOLD_HUMAN_OVERRIDE_RISE_PCT: float = 10.0
THRESHOLD_RECURRENCE_RISE_PCT: float = 20.0
THRESHOLD_IMPROVEMENT_PCT: float = 5.0


class EffectEvaluationService:
    """Effect 评估应用层。"""

    def __init__(
        self,
        client: EffectMetricClient | None = None,
        repository: EffectMetricRepository | None = None,
    ) -> None:
        self._client = client or EffectMetricClient()
        self._repository = repository or EffectMetricRepository(_DEFAULT_SESSION_FACTORY)

    # -------------------------------------------------------------- record ----

    async def record_current_snapshot(
        self,
        period_start: str,
        period_end: str,
        *,
        scope_key: str = "_",
    ) -> EffectMetric:
        """拉 Java effect 端点 → upsert 到 ``memory_metric_snapshot``。"""
        raw_items: list[dict[str, Any]] = []
        async for page in self._client.iter_all_pages(
            period_start, period_end, size=100, scope_key=scope_key,
        ):
            raw_items.extend(page.get("items") or [])

        if not raw_items:
            raise ValueError(
                f"effect endpoint 在 [{period_start}, {period_end}] scope={scope_key} 无返回",
            )

        # 多条 raw → 求平均（与 coverage 的 list_snapshots 行为对齐）。
        merged: dict[str, float] = {k: 0.0 for k in EFFECT_METRIC_KEYS}
        for raw in raw_items:
            merged[METRIC_AI_PASS_RATE] += float(raw.get("aiPassRate") or 0.0)
            merged[METRIC_HUMAN_OVERRIDE_RATE] += float(raw.get("humanOverrideRate") or 0.0)
            merged[METRIC_RECURRENCE_RATE_30D] += float(raw.get("recurrenceRate30d") or 0.0)
            merged[METRIC_COVERAGE_GAP_COUNT] += float(raw.get("coverageGapCount") or 0.0)
        n = max(1, len(raw_items))
        for k in merged:
            merged[k] = round(merged[k] / n, 4)

        metric_value = {k: merged[k] for k in EFFECT_METRIC_KEYS}
        return await self._repository.upsert_snapshot(
            period_start=period_start,
            period_end=period_end,
            scope_key=scope_key,
            metric_value=metric_value,
        )

    # -------------------------------------------------------------- compare ----

    async def compare_periods(
        self,
        baseline_start: str,
        baseline_end: str,
        current_start: str,
        current_end: str,
        *,
        scope_key: str = "_",
    ) -> list[EffectComparison]:
        """读两侧 snapshot → 按 metric_value 字典 diff → 列表。"""
        baseline = await self._repository.fetch_by_period(
            baseline_start, baseline_end, scope_key=scope_key,
        )
        current = await self._repository.fetch_by_period(
            current_start, current_end, scope_key=scope_key,
        )
        if baseline is None or current is None:
            raise ValueError(
                f"对比周期缺失：baseline={baseline is None} current={current is None}",
            )
        return self._diff(baseline, current)

    @staticmethod
    def compute_delta_pct(
        baseline: EffectMetric, current: EffectMetric,
    ) -> float:
        """整体 ``metric_value`` 平均变化（百分点）。"""
        b_vals = baseline.metric_value or {}
        c_vals = current.metric_value or {}
        if not b_vals:
            return 0.0
        deltas: list[float] = []
        for k, b_v in b_vals.items():
            c_v = c_vals.get(k)
            if c_v is None or b_v == 0:
                continue
            deltas.append((c_v - b_v) / abs(b_v) * 100.0)
        return round(sum(deltas) / len(deltas), 4) if deltas else 0.0

    def _diff(
        self, baseline: EffectMetric, current: EffectMetric,
    ) -> list[EffectComparison]:
        b_dict = dict(baseline.metric_value or {})
        c_dict = dict(current.metric_value or {})
        out: list[EffectComparison] = []
        for key in EFFECT_METRIC_KEYS:
            b_val = b_dict.get(key)
            c_val = c_dict.get(key)
            if b_val is None or c_val is None:
                continue
            delta = self._delta_pct_value(float(b_val), float(c_val))
            significance = self._significance_for(key, delta)
            out.append(
                EffectComparison(
                    baseline=EffectMetric(
                        metric_key=f"{key}_baseline",
                        metric_value={key: float(b_val)},
                        period_start=baseline.period_start,
                        period_end=baseline.period_end,
                        scope_key=baseline.scope_key,
                        recorded_at=baseline.recorded_at,
                    ),
                    current=EffectMetric(
                        metric_key=f"{key}_current",
                        metric_value={key: float(c_val)},
                        period_start=current.period_start,
                        period_end=current.period_end,
                        scope_key=current.scope_key,
                        recorded_at=current.recorded_at,
                    ),
                    delta_pct=round(delta, 4),
                    significance=significance,
                ),
            )
        return out

    @staticmethod
    def _extract_metric_dict(metric: EffectMetric) -> dict[str, float]:
        """``EffectMetric.metric_value`` 现在是 dict——直接返回。"""
        return dict(metric.metric_value or {})

    @staticmethod
    def _delta_pct_value(b: float, c: float) -> float:
        if b == 0.0:
            return 100.0 if c != 0.0 else 0.0
        return (c - b) / abs(b) * 100.0

    @staticmethod
    def _significance_for(metric_key: str, delta_pct: float) -> str:
        """按 metric 类型 + 阈值打标。

        - ai_pass_rate 下降 >5% → regression；
        - human_override_rate 上升 >10% → regression；
        - recurrence_rate_30d 上升 >20% → regression；
        - 反向越线 → improvement；
        - 其它 → stable。
        """
        if metric_key == METRIC_AI_PASS_RATE:
            if delta_pct <= -THRESHOLD_AI_PASS_DROP_PCT:
                return "regression"
            if delta_pct >= THRESHOLD_IMPROVEMENT_PCT:
                return "improvement"
            return "stable"
        if metric_key == METRIC_HUMAN_OVERRIDE_RATE:
            if delta_pct >= THRESHOLD_HUMAN_OVERRIDE_RISE_PCT:
                return "regression"
            if delta_pct <= -THRESHOLD_IMPROVEMENT_PCT:
                return "improvement"
            return "stable"
        if metric_key == METRIC_RECURRENCE_RATE_30D:
            if delta_pct >= THRESHOLD_RECURRENCE_RISE_PCT:
                return "regression"
            if delta_pct <= -THRESHOLD_IMPROVEMENT_PCT:
                return "improvement"
            return "stable"
        # 其它（coverage_gap_count 等）
        if delta_pct >= THRESHOLD_HUMAN_OVERRIDE_RISE_PCT:
            return "regression"
        if delta_pct <= -THRESHOLD_IMPROVEMENT_PCT:
            return "improvement"
        return "stable"

    # -------------------------------------------------------------- regression ----

    async def detect_regression(
        self,
        period_start: str,
        period_end: str,
        *,
        scope_key: str = "_",
        baseline_days: int = 30,
    ) -> list[str]:
        """拉 current → 与历史 baseline 比对 → 标记 regression 的 metric_key。

        baseline 取 ``period_start`` 之前 ``baseline_days`` 天的 snapshot；若不存在
        则返回空列表（视为无足够历史）。
        """
        current = await self._repository.fetch_by_period(
            period_start, period_end, scope_key=scope_key,
        )
        if current is None:
            return []

        # 尝试若干 baseline 候选（list_snapshots 取前 N 条最近 7d 内的）；
        # 简化口径：取 scope 全部历史中 recorded_at 早于 current 的最近一条。
        candidates = await self._repository.list_snapshots(scope_key=scope_key, limit=20)
        baseline: EffectMetric | None = None
        for c in candidates:
            if (
                c.recorded_at < current.recorded_at
                and c.scope_key == scope_key
                and not (
                    c.period_start == current.period_start
                    and c.period_end == current.period_end
                )
            ):
                baseline = c
                break
        if baseline is None:
            return []

        diffs = self._diff_full(baseline, current)
        return [
            metric_key
            for metric_key, sig, _delta in diffs
            if sig == "regression"
        ]

    def _diff_full(
        self, baseline: EffectMetric, current: EffectMetric,
    ) -> list[tuple[str, str, float]]:
        """返回 (metric_key, significance, delta_pct) 列表——完整 4 metric + 标记。"""
        b_dict = self._extract_metric_dict(baseline)
        c_dict = self._extract_metric_dict(current)
        out: list[tuple[str, str, float]] = []
        for k in EFFECT_METRIC_KEYS:
            b = b_dict.get(k)
            c = c_dict.get(k)
            if b is None or c is None:
                continue
            delta = self._delta_pct_value(float(b), float(c))
            sig = self._significance_for(k, delta)
            out.append((k, sig, delta))
        return out


def _as_dict(metric_value: dict[str, Any] | None) -> dict[str, float]:
    if isinstance(metric_value, dict):
        return {k: float(v) for k, v in metric_value.items() if isinstance(v, (int, float))}
    return {}


# 默认 session_factory——正式环境由调用方注入；测试时 override。
try:
    from app.core.datasource import datasources as _shared  # noqa: WPS433
    _DEFAULT_SESSION_FACTORY = _shared.get_session_factory("postgres_primary")
except Exception:  # pragma: no cover — 启动期降级
    _DEFAULT_SESSION_FACTORY = None  # type: ignore[assignment]


__all__ = [
    "EffectEvaluationService",
    "THRESHOLD_AI_PASS_DROP_PCT",
    "THRESHOLD_HUMAN_OVERRIDE_RISE_PCT",
    "THRESHOLD_IMPROVEMENT_PCT",
    "THRESHOLD_RECURRENCE_RISE_PCT",
    "build_effect_metric_key",
]


# 防未引用报警
_ = Iterable
_ = datetime
_ = EffectComparison