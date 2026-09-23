"""Coverage 域模型。"""
from __future__ import annotations

from app.skill.coverage.domain.models import (
    CoverageSnapshot,
    CoverageSummary,
    FrequencyTrend,
    GapItem,
    RecurrenceItem,
    RegionSupervisorLoad,
    SnapshotFilters,
    build_metric_key,
    snapshot_from_metric_value,
)

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
]