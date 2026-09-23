"""Effect Evaluation 域 dataclass（FR-12）。

- ``EffectMetric`` 一条周期内 snapshot，``metric_value`` 是 dict，存四个 metric：
  ``ai_pass_rate`` / ``human_override_rate`` / ``recurrence_rate_30d`` /
  ``coverage_gap_count``（与 ``memory_metric_snapshot.metric_value`` 字段对齐）；
- ``EffectComparison`` 跨周期对比（baseline vs current → delta_pct + significance）；
- ``build_effect_metric_key(period_start, period_end, scope_key)`` 按
  ``effect_evaluation_<start>_<end>_<scope>`` 生成唯一 metric_key。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- 四 metric --


METRIC_AI_PASS_RATE: str = "ai_pass_rate"
METRIC_HUMAN_OVERRIDE_RATE: str = "human_override_rate"
METRIC_RECURRENCE_RATE_30D: str = "recurrence_rate_30d"
METRIC_COVERAGE_GAP_COUNT: str = "coverage_gap_count"

EFFECT_METRIC_KEYS: tuple[str, ...] = (
    METRIC_AI_PASS_RATE,
    METRIC_HUMAN_OVERRIDE_RATE,
    METRIC_RECURRENCE_RATE_30D,
    METRIC_COVERAGE_GAP_COUNT,
)


def _safe(s: str) -> str:
    """与 coverage.build_metric_key 同款清洗（去掉非 URL/JSON 安全字符）。"""
    return (
        s.replace(":", "")
        .replace("+", "p")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )


# ---------------------------------------------------------------- models --


@dataclass(slots=True)
class EffectMetric:
    """周期内 effect snapshot（metric_key 唯一）。

    - ``metric_key`` ``effect_evaluation_<start>_<end>_<scope>``；
    - ``metric_value`` dict 存四个 metric 值（与 ``memory_metric_snapshot.metric_value`` 对齐）；
    - ``scope_key`` 工厂/区域/分类等的复合标识（缺省 = 全局）。
    """

    metric_key: str
    metric_value: dict[str, float]
    period_start: str
    period_end: str
    scope_key: str
    recorded_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "metric_value": dict(self.metric_value),
            "period_start": self.period_start,
            "period_end": self.period_end,
            "scope_key": self.scope_key,
            "recorded_at": self.recorded_at,
        }


@dataclass(slots=True)
class EffectComparison:
    """跨周期对比结果。

    - ``baseline / current`` 两侧 ``EffectMetric``；
    - ``delta_pct`` 0~N，>0 表示当前 > 基线；
    - ``significance`` ``"regression"`` / ``"improvement"`` / ``"stable"``，
      由 service 按阈值口径打标。
    """

    baseline: EffectMetric
    current: EffectMetric
    delta_pct: float
    significance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "current": self.current.to_dict(),
            "delta_pct": round(self.delta_pct, 4),
            "significance": self.significance,
        }


# ---------------------------------------------------------------- helpers --


def build_effect_metric_key(
    period_start: str,
    period_end: str,
    scope_key: str = "_",
) -> str:
    """``effect_evaluation_<safe_start>_<safe_end>_<scope>``。"""
    s = _safe(period_start)
    e = _safe(period_end)
    scope = _safe(scope_key) if scope_key else "_"
    return f"effect_evaluation_{s}_{e}_{scope}"


def effect_metric_from_metric_value(
    metric_key: str,
    raw: dict[str, Any],
) -> EffectMetric:
    """从 ``memory_metric_snapshot.metric_value`` 字典还原 ``EffectMetric``。

    ``raw`` 已是 dict（``metric_value`` 列内容）：里面既包含四 metric
    ``ai_pass_rate`` / ``human_override_rate`` / ``recurrence_rate_30d`` /
    ``coverage_gap_count``，也含 ``period_start / period_end / scope_key / kind /
    recorded_at`` 等元信息。本函数只把四 metric 抽到 ``metric_value`` 字段。
    """
    period_start = str(raw.get("period_start") or "")
    period_end = str(raw.get("period_end") or "")
    scope_key = str(raw.get("scope_key") or "_")
    metrics: dict[str, float] = {}
    for k in EFFECT_METRIC_KEYS:
        v = raw.get(k)
        if isinstance(v, (int, float)):
            metrics[k] = float(v)
    return EffectMetric(
        metric_key=metric_key,
        metric_value=metrics,
        period_start=period_start,
        period_end=period_end,
        scope_key=scope_key,
        recorded_at=str(raw.get("recorded_at") or _utcnow_iso()),
    )


__all__ = [
    "EFFECT_METRIC_KEYS",
    "EffectComparison",
    "EffectMetric",
    "METRIC_AI_PASS_RATE",
    "METRIC_COVERAGE_GAP_COUNT",
    "METRIC_HUMAN_OVERRIDE_RATE",
    "METRIC_RECURRENCE_RATE_30D",
    "build_effect_metric_key",
    "effect_metric_from_metric_value",
]