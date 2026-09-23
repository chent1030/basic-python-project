"""Coverage 应用层：四分析 + summary 聚合 + ingestion 留口。

四分析算法（与 Java 端聚合契约对齐）：
- ``analyze_frequency``：拉 frequency 全量 → 转 CoverageSnapshot list → 按
  ``(factory, area, category_l1_id)`` 三元组聚合得到 FrequencyTrend 列表，
  issue_count desc 取 top 5；
- ``analyze_region_supervisor_load``：拉 region-supervisor 全量 → 主管在手/超期
  排序取 top 10；
- ``analyze_recurrence``：拉 recurrence → recurrence_count desc 取 top 5；
- ``analyze_gaps``：拉 gaps → 按 ``storage_room_type`` 聚合 → gap_severity desc
  取 top 10。

异常指标（内置于 ``CoverageSummary.anomalies``）：
- top_3_high_freq：FrequencyTrend 前 3；
- top_3_overdue_loading：RegionSupervisorLoad 按 overdue_count desc 前 3；
- top_3_recurrence：RecurrenceItem 前 3；
- gap_count_by_type：{storage_room_type: gap_count}（用于分布看板）。

``CoverageIngestionService`` 留口给 FR-12 评估侧消费：本波不消费，但接口完整；
``record_snapshot`` 单调 upsert 到 ``memory_metric_snapshot``。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.skill.coverage.domain.models import (
    CoverageSnapshot,
    CoverageSummary,
    FrequencyTrend,
    GapItem,
    RecurrenceItem,
    RegionSupervisorLoad,
    SnapshotFilters,
)
from app.skill.coverage.infrastructure.java_client import CoverageClient
from app.skill.coverage.infrastructure.repository import CoverageRepository

logger = logging.getLogger(__name__)


_TOP_K_FREQUENCY: int = 5
_TOP_K_REG_SUP: int = 10
_TOP_K_RECUR: int = 5
_TOP_K_GAP: int = 10
_TOP_K_ANOMALY: int = 3


def _filter_dict(filters: SnapshotFilters | None) -> dict[str, Any]:
    """把 SnapshotFilters 转成仅 None-free dict，用于构造分页/限速。"""
    if filters is None:
        return {}
    d = filters.to_dict()
    return {k: v for k, v in d.items() if v is not None}


class CoverageAnalysisService:
    """Coverage 分析服务：四分析 + summary。"""

    def __init__(
        self,
        client: CoverageClient,
        repository: CoverageRepository | None = None,
    ) -> None:
        self._client = client
        self._repo = repository

    @property
    def client(self) -> CoverageClient:
        return self._client

    @property
    def repository(self) -> CoverageRepository | None:
        return self._repo

    # -------------------------------------------------------------- frequency ----

    async def analyze_frequency(
        self,
        period_start: str,
        period_end: str,
        filters: SnapshotFilters | None = None,
        *,
        top_k: int = _TOP_K_FREQUENCY,
    ) -> list[FrequencyTrend]:
        """frequency 端点全量分页 → 按 (factory, area, category_l1_id) 聚合 → top_k。

        返回按 ``issue_count desc, overdue_count desc`` 排序的 FrequencyTrend 列表。
        """
        raw_items = await self._collect_all_pages(
            self._client.fetch_frequency,
            period_start, period_end,
            factory=filters.factory if filters else None,
            area=filters.area if filters else None,
            category_l1_id=filters.category_l1_id if filters else None,
        )
        buckets: dict[tuple[str, str, int], dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            factory = str(item.get("factory") or "")
            area = str(item.get("area") or "")
            cat = int(item.get("category_l1_id") or 0)
            key = (factory, area, cat)
            b = buckets.setdefault(
                key,
                {
                    "factory": factory,
                    "area": area,
                    "category_l1_id": cat,
                    "issue_count": 0,
                    "closed_count": 0,
                    "overdue_count": 0,
                    "recurrence_count": 0,
                },
            )
            b["issue_count"] += int(item.get("issue_count") or 0)
            b["closed_count"] += int(item.get("closed_count") or 0)
            b["overdue_count"] += int(item.get("overdue_count") or 0)
            b["recurrence_count"] += int(item.get("recurrence_count") or 0)
        out: list[FrequencyTrend] = []
        for b in buckets.values():
            ic = b["issue_count"]
            cc = b["closed_count"]
            rate = (cc / ic) if ic > 0 else 0.0
            out.append(
                FrequencyTrend(
                    factory=b["factory"],
                    area=b["area"],
                    category_l1_id=b["category_l1_id"],
                    issue_count=ic,
                    closed_count=cc,
                    close_rate=round(rate, 4),
                    overdue_count=b["overdue_count"],
                    recurrence_count=b["recurrence_count"],
                ),
            )
        out.sort(key=lambda t: (-t.issue_count, -t.overdue_count))
        return out[:top_k]

    # -------------------------------------------------------------- region-supervisor ----

    async def analyze_region_supervisor_load(
        self,
        period_start: str,
        period_end: str,
        filters: SnapshotFilters | None = None,
        *,
        top_k: int = _TOP_K_REG_SUP,
    ) -> list[RegionSupervisorLoad]:
        """region-supervisor 端点 → 按 (overdue_count desc, open_count desc) 取 top_k。"""
        raw_items = await self._collect_all_pages(
            self._client.fetch_region_supervisor,
            period_start, period_end,
            region=filters.region if filters else None,
        )
        rows: list[RegionSupervisorLoad] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            region = str(item.get("region") or "")
            sup = str(item.get("supervisor_emp_no") or "")
            rows.append(
                RegionSupervisorLoad(
                    region=region,
                    supervisor_emp_no=sup,
                    open_count=int(item.get("open_count") or 0),
                    overdue_count=int(item.get("overdue_count") or 0),
                    handled_count=int(item.get("handled_count") or 0),
                ),
            )
        rows.sort(key=lambda r: (-r.overdue_count, -r.open_count))
        return rows[:top_k]

    # -------------------------------------------------------------- recurrence ----

    async def analyze_recurrence(
        self,
        period_start: str,
        period_end: str,
        filters: SnapshotFilters | None = None,
        *,
        threshold: int = 2,
        top_k: int = _TOP_K_RECUR,
    ) -> list[RecurrenceItem]:
        """recurrence 端点 → recurrence_count desc 取 top_k。

        ``filters`` 在 recurrence 端点不直接生效（Java 端无对应 query 参数），保留
        以求接口对齐——若 filters 不为空，签到 fallback 仅影响 metric_value 但不影响本结果。
        """
        raw_items = await self._collect_all_pages(
            self._client.fetch_recurrence,
            period_start, period_end,
            threshold=threshold,
        )
        rows: list[RecurrenceItem] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            rc = int(item.get("recurrence_count") or 0)
            if rc < threshold:
                continue
            rows.append(
                RecurrenceItem(
                    issue_id=int(item.get("issue_id") or 0),
                    recurrence_count=rc,
                    last_recurrence_at=item.get("last_recurrence_at"),
                    category_l1_id=item.get("category_l1_id"),
                    factory=item.get("factory"),
                    area=item.get("area"),
                ),
            )
        rows.sort(key=lambda r: (-r.recurrence_count, r.issue_id))
        return rows[:top_k]

    # -------------------------------------------------------------- gaps ----

    async def analyze_gaps(
        self,
        period_start: str,
        period_end: str,
        filters: SnapshotFilters | None = None,
        *,
        top_k: int = _TOP_K_GAP,
    ) -> list[GapItem]:
        """gaps 端点 → 按 storage_room_type 聚合 gap_severity → desc 取 top_k。"""
        raw_items = await self._collect_all_pages(
            self._client.fetch_gaps,
            period_start, period_end,
            factory=filters.factory if filters else None,
            area=filters.area if filters else None,
            category_l1_id=filters.category_l1_id if filters else None,
        )
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "gap_count": 0,
                "max_severity": 0,
                "max_days": 0,
                "category_l1_id": None,
                "factory": None,
                "area": None,
            },
        )
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            srt = str(item.get("storage_room_type") or "")
            sev = int(item.get("gap_severity") or 0)
            days = int(item.get("days_since_last_record") or 0)
            b = buckets[srt]
            b["gap_count"] += 1
            if sev > b["max_severity"]:
                b["max_severity"] = sev
                b["category_l1_id"] = item.get("category_l1_id")
                b["factory"] = item.get("factory")
                b["area"] = item.get("area")
            if days > b["max_days"]:
                b["max_days"] = days
        out: list[GapItem] = []
        for srt, b in buckets.items():
            out.append(
                GapItem(
                    storage_room_type=srt,
                    days_since_last_record=b["max_days"],
                    gap_severity=b["max_severity"],
                    category_l1_id=b["category_l1_id"],
                    factory=b["factory"],
                    area=b["area"],
                ),
            )
        out.sort(key=lambda g: (-g.gap_severity, -g.days_since_last_record))
        return out[:top_k]

    # -------------------------------------------------------------- summary ----

    async def summary(
        self,
        period_start: str,
        period_end: str,
        filters: SnapshotFilters | None = None,
        *,
        kinds: list[str] | None = None,
    ) -> CoverageSummary:
        """四分析 + 异常指标聚合。

        ``kinds``：None = 全部分析；可选子集 ``['frequency','region_supervisor',
         'recurrence','gaps']``。
        """
        requested = set(kinds or ["frequency", "region_supervisor", "recurrence", "gaps"])
        unsupported = requested - {
            "frequency", "region_supervisor", "recurrence", "gaps",
        }
        if unsupported:
            raise ValueError(f"未知 kinds: {sorted(unsupported)}")

        freq: list[FrequencyTrend] = []
        loads: list[RegionSupervisorLoad] = []
        recs: list[RecurrenceItem] = []
        gaps: list[GapItem] = []

        if "frequency" in requested:
            freq = await self.analyze_frequency(period_start, period_end, filters)
        if "region_supervisor" in requested:
            loads = await self.analyze_region_supervisor_load(
                period_start, period_end, filters,
            )
        if "recurrence" in requested:
            recs = await self.analyze_recurrence(period_start, period_end, filters)
        if "gaps" in requested:
            gaps = await self.analyze_gaps(period_start, period_end, filters)

        total_issues = sum(t.issue_count for t in freq)
        total_gaps = len(gaps)
        rates = [t.close_rate for t in freq if t.issue_count > 0]
        avg_close_rate = (sum(rates) / len(rates)) if rates else 0.0
        rec_counts = sorted(r.recurrence_count for r in recs)
        p50 = rec_counts[len(rec_counts) // 2] if rec_counts else 0

        summary_metrics = {
            "total_issues": total_issues,
            "total_gaps": total_gaps,
            "avg_close_rate": round(avg_close_rate, 4),
            "p50_recur_count": p50,
        }

        # 异常指标
        top_3_high_freq = freq[:_TOP_K_ANOMALY]
        top_3_overdue = sorted(loads, key=lambda r: -r.overdue_count)[:_TOP_K_ANOMALY]
        top_3_recur = recs[:_TOP_K_ANOMALY]
        gap_count_by_type: dict[str, int] = {}
        for g in gaps:
            gap_count_by_type[g.storage_room_type] = (
                gap_count_by_type.get(g.storage_room_type, 0) + 1
            )

        anomalies = {
            "top_3_high_freq": [t.to_dict() for t in top_3_high_freq],
            "top_3_overdue_loading": [r.to_dict() for r in top_3_overdue],
            "top_3_recurrence": [r.to_dict() for r in top_3_recur],
            "gap_count_by_type": dict(gap_count_by_type),
        }

        return CoverageSummary(
            period_start=period_start,
            period_end=period_end,
            frequency_trends=freq,
            region_supervisor_loads=loads,
            recurrences=recs,
            gaps=gaps,
            summary_metrics=summary_metrics,
            anomalies=anomalies,
        )

    # -------------------------------------------------------------- helpers ----

    async def _collect_all_pages(
        self,
        fetch_one: Any,
        start_iso: str,
        end_iso: str,
        *,
        threshold: int | None = None,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
        region: str | None = None,
    ) -> list[dict[str, Any]]:
        """带过滤参数的全量分页收集。

        注意 CoverageClient 的 fetch_* 方法已支持 kwargs 传入过滤；通过 inspect 探测签名
        适配四种端点（frequency/gaps 接 factory/area/category_l1_id；region-supervisor
        接 region；recurrence 接 threshold）。
        """
        import inspect

        params = inspect.signature(fetch_one).parameters

        def _kwargs(page: int) -> dict[str, Any]:
            kw: dict[str, Any] = {"page": page}
            if "factory" in params and factory is not None:
                kw["factory"] = factory
            if "area" in params and area is not None:
                kw["area"] = area
            if "category_l1_id" in params and category_l1_id is not None:
                kw["category_l1_id"] = category_l1_id
            if "region" in params and region is not None:
                kw["region"] = region
            if "threshold" in params and threshold is not None:
                kw["threshold"] = threshold
            return kw

        out: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = await fetch_one(start_iso, end_iso, **_kwargs(page))
            items = resp.get("items") or resp.get("records") or []
            out.extend(items)
            total_pages = int(resp.get("total_pages") or 0)
            page += 1
            if page > total_pages or total_pages <= 0:
                break
        return out


class CoverageIngestionService:
    """Coverage 评估 ingestion 留口（FR-12 消费侧未接入）。

    ``record_snapshot`` 落 ``memory_metric_snapshot`` 表——同周期幂等，由 metric_key 决定。
    """

    def __init__(
        self,
        repository: CoverageRepository,
    ) -> None:
        self._repo = repository

    async def record_snapshot(self, snapshot: CoverageSnapshot) -> CoverageSnapshot:
        """upsert 一条快照；返回持久化后对象。"""
        return await self._repo.upsert_snapshot(snapshot)


__all__ = [
    "CoverageAnalysisService",
    "CoverageIngestionService",
]