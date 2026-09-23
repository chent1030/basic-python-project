"""Coverage 域 dataclass。

设计要点：
- ``CoverageSnapshot`` 是单条「(factory, area, category_l1_id) 在周期内的统计快照」；
  字段口径对齐 Java 端 ``/api/cps/admin/coverage/frequency`` 返回；
- ``CoverageSummary`` 是聚合视图，包含四分析结果 + 异常指标；
- 异常指标（top_3_high_freq / top_3_overdue_loading / top_3_recurrence / gap_count_by_type）
  单独从四分析 top_k 里截前 3，供告警卡需要快速 lock 高风险面；
- ``metric_key='coverage_analysis_<start>_<end>'`` 是 ``memory_metric_snapshot`` 表的
  唯一键；upsert 路径由 CoverageRepository 实现。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(s: str | datetime | None) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    if isinstance(s, str):
        return datetime.fromisoformat(s)
    return None


@dataclass(slots=True)
class SnapshotFilters:
    """周期内的可选过滤条件（factory/area/categoryL1Id/region/supervisor_emp_no）。"""

    factory: str | None = None
    area: str | None = None
    category_l1_id: int | None = None
    category_l2_id: int | None = None
    region: str | None = None
    supervisor_emp_no: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factory": self.factory,
            "area": self.area,
            "category_l1_id": self.category_l1_id,
            "category_l2_id": self.category_l2_id,
            "region": self.region,
            "supervisor_emp_no": self.supervisor_emp_no,
        }


@dataclass(slots=True)
class CoverageSnapshot:
    """Java frequency 端点一行 → Python 内存表示。

    字段口径：
    - ``snapshot_id`` 本服务生成的 UUID4（仅 Python 侧，DB 不强制唯一）；
    - ``period_start/period_end`` 周期 ISO；
    - ``issue_count`` 周期内累计；``recent_count`` 最近 N 日内新增；
    - ``close_rate`` 已关闭 / 累计，0~1；``overdue_count`` 超期未关闭；
    - ``recurrence_count`` 周期内复发次数；
    - ``days_since_last_record`` 最近记录距 period_end 的天数；
    - ``gap_severity`` 0/1/2 三档（OK/WARN/CRITICAL）。
    """

    snapshot_id: str
    period_start: str
    period_end: str
    factory: str | None
    area: str | None
    category_l1_id: int | None
    category_l2_id: int | None = None
    region: str | None = None
    supervisor_emp_no: str | None = None
    issue_count: int = 0
    recent_count: int = 0
    closed_count: int = 0
    close_rate: float = 0.0
    open_count: int = 0
    handled_count: int = 0
    overdue_count: int = 0
    recurrence_count: int = 0
    last_recurrence_at: str | None = None
    days_since_last_record: int | None = None
    last_record_at: str | None = None
    gap_severity: int = 0
    recorded_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "factory": self.factory,
            "area": self.area,
            "category_l1_id": self.category_l1_id,
            "category_l2_id": self.category_l2_id,
            "region": self.region,
            "supervisor_emp_no": self.supervisor_emp_no,
            "issue_count": self.issue_count,
            "recent_count": self.recent_count,
            "closed_count": self.closed_count,
            "close_rate": self.close_rate,
            "open_count": self.open_count,
            "handled_count": self.handled_count,
            "overdue_count": self.overdue_count,
            "recurrence_count": self.recurrence_count,
            "last_recurrence_at": self.last_recurrence_at,
            "days_since_last_record": self.days_since_last_record,
            "last_record_at": self.last_record_at,
            "gap_severity": self.gap_severity,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CoverageSnapshot:
        return cls(
            snapshot_id=str(raw.get("snapshot_id") or raw.get("id") or ""),
            period_start=str(raw.get("period_start") or ""),
            period_end=str(raw.get("period_end") or ""),
            factory=raw.get("factory"),
            area=raw.get("area"),
            category_l1_id=raw.get("category_l1_id"),
            category_l2_id=raw.get("category_l2_id"),
            region=raw.get("region"),
            supervisor_emp_no=raw.get("supervisor_emp_no"),
            issue_count=int(raw.get("issue_count") or 0),
            recent_count=int(raw.get("recent_count") or 0),
            closed_count=int(raw.get("closed_count") or 0),
            close_rate=float(raw.get("close_rate") or 0.0),
            open_count=int(raw.get("open_count") or 0),
            handled_count=int(raw.get("handled_count") or 0),
            overdue_count=int(raw.get("overdue_count") or 0),
            recurrence_count=int(raw.get("recurrence_count") or 0),
            last_recurrence_at=raw.get("last_recurrence_at"),
            days_since_last_record=(
                int(raw["days_since_last_record"])
                if raw.get("days_since_last_record") is not None
                else None
            ),
            last_record_at=raw.get("last_record_at"),
            gap_severity=int(raw.get("gap_severity") or 0),
            recorded_at=str(raw.get("recorded_at") or _utcnow_iso()),
        )


@dataclass(slots=True)
class FrequencyTrend:
    """frequency 端点聚合后的一行（按 factory/area/category_l1_id 分组）。"""

    factory: str
    area: str
    category_l1_id: int
    issue_count: int
    closed_count: int
    close_rate: float
    overdue_count: int
    recurrence_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RegionSupervisorLoad:
    """region-supervisor 端点一行 → 主管负载。"""

    region: str
    supervisor_emp_no: str
    open_count: int
    overdue_count: int
    handled_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RecurrenceItem:
    """recurrence 端点一行 → 最常复发问题。"""

    issue_id: int
    recurrence_count: int
    last_recurrence_at: str | None
    category_l1_id: int | None = None
    factory: str | None = None
    area: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GapItem:
    """gaps 端点一行 → 覆盖缺口（按 storageRoomType 聚合）。"""

    storage_room_type: str
    days_since_last_record: int
    gap_severity: int
    category_l1_id: int | None = None
    factory: str | None = None
    area: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CoverageSummary:
    """聚合视图（异常指标内嵌）。"""

    period_start: str
    period_end: str
    frequency_trends: list[FrequencyTrend] = field(default_factory=list)
    region_supervisor_loads: list[RegionSupervisorLoad] = field(default_factory=list)
    recurrences: list[RecurrenceItem] = field(default_factory=list)
    gaps: list[GapItem] = field(default_factory=list)
    summary_metrics: dict[str, Any] = field(default_factory=dict)
    anomalies: dict[str, list[Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "frequency_trends": [t.to_dict() for t in self.frequency_trends],
            "region_supervisor_loads": [r.to_dict() for r in self.region_supervisor_loads],
            "recurrences": [r.to_dict() for r in self.recurrences],
            "gaps": [g.to_dict() for g in self.gaps],
            "summary_metrics": dict(self.summary_metrics),
            "anomalies": {k: list(v) for k, v in self.anomalies.items()},
        }


# ---------------------------------------------------------------- metric_key helpers --


def build_metric_key(period_start: str, period_end: str) -> str:
    """``memory_metric_snapshot`` 表的 metric_key 唯一键。

    形式：``coverage_analysis_<period_start>_<period_end>``——同一周期内 upsert 幂等。
    """
    safe_start = period_start.replace(":", "").replace("+", "p").replace("-", "-")
    safe_end = period_end.replace(":", "").replace("+", "p").replace("-", "-")
    return f"coverage_analysis_{safe_start}_{safe_end}"


def snapshot_from_metric_value(raw: dict[str, Any]) -> CoverageSnapshot:
    """``memory_metric_snapshot.metric_value``（JSONB dict）→ CoverageSnapshot。"""
    if not isinstance(raw, dict):
        return CoverageSnapshot.from_dict({})
    if "snapshot" in raw and isinstance(raw["snapshot"], dict):
        return CoverageSnapshot.from_dict(raw["snapshot"])
    return CoverageSnapshot.from_dict(raw)


def to_jsonb_safe(obj: Any) -> Any:
    """JSONB 列安全序列化（datetime/dataclass/list → dict/list/str）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, CoverageSnapshot):
        return obj.to_dict()
    if isinstance(obj, CoverageSummary):
        return obj.to_dict()
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: to_jsonb_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonb_safe(v) for v in obj]
    try:
        return json.loads(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return str(obj)


__all__ = [
    "CoverageSnapshot",
    "CoverageSummary",
    "FrequencyTrend",
    "GapItem",
    "RecurrenceItem",
    "RegionSupervisorLoad",
    "SnapshotFilters",
    "build_metric_key",
    "snapshot_from_metric_value",
    "to_jsonb_safe",
]


# satisfy ruff unused-import check
_ = _parse_iso