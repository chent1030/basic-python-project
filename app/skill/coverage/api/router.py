"""Coverage 分析 FastAPI 路由（FR-10）。

路径（位于 ``/api/v1/coverage/``）：
- GET  /coverage/analyze   四分析聚合 + 异常指标
- POST /coverage/snapshot   单跑一次分析 + 落 ``memory_metric_snapshot``

鉴权：``Identity.require('cps_admin')``（与 memory 同模式）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.core.datasource import DatasourceManager
from app.core.datasource import datasources as shared_datasources
from app.skill.coverage.application.services import (
    CoverageAnalysisService,
    CoverageIngestionService,
)
from app.skill.coverage.domain.models import (
    CoverageSnapshot,
    SnapshotFilters,
)
from app.skill.coverage.infrastructure.java_client import CoverageClient
from app.skill.coverage.infrastructure.repository import CoverageRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coverage", tags=["skill-coverage"], route_class=FrameworkRoute)


# ---------------------------------------------------------------- request/response models --


class AnalyzeResponse(BaseModel):
    period_start: str
    period_end: str
    frequency_trends: list[dict[str, Any]] = Field(default_factory=list)
    region_supervisor_loads: list[dict[str, Any]] = Field(default_factory=list)
    recurrences: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    summary_metrics: dict[str, Any] = Field(default_factory=dict)
    anomalies: dict[str, Any] = Field(default_factory=dict)


class SnapshotResponse(BaseModel):
    snapshot_id: str
    period_start: str
    period_end: str
    recorded_at: str
    summary_metrics: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- service locator --


def _coverage_services(request: Request) -> tuple[
    CoverageAnalysisService, CoverageIngestionService,
]:
    """懒建服务实例并挂 app.state。"""
    cached = getattr(request.app.state, "coverage_services", None)
    if cached is not None:
        return cached

    repository: CoverageRepository | None = None
    try:
        manager: DatasourceManager = shared_datasources
        session_factory = manager.get_session_factory("postgres_primary")
        repository = CoverageRepository(session_factory)
    except KeyError:
        repository = None  # 纯计算模式；本波 GET /analyze 不依赖 repo，但 POST /snapshot 需要

    client = CoverageClient()
    analysis = CoverageAnalysisService(client, repository=repository)
    ingestion = CoverageIngestionService(repository) if repository is not None else None
    services = (analysis, ingestion)
    request.app.state.coverage_services = services
    return services


ServicesDep = Annotated[
    tuple[CoverageAnalysisService, CoverageIngestionService | None],
    Depends(_coverage_services),
]


def _parse_iso(s: str) -> str:
    """校验 ISO 字符串合法；非 ISO 直接 422。"""
    try:
        datetime.fromisoformat(s)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ISO 8601 时间格式无法识别: {s}",
        ) from exc
    return s


# ---------------------------------------------------------------- endpoints --


@router.get("/analyze", response_model=AnalyzeResponse)
async def analyze(
    identity: Identity,
    services: ServicesDep,
    periodStart: str = Query(..., description="ISO 8601 起点"),
    periodEnd: str = Query(..., description="ISO 8601 终点"),
    kinds: str | None = Query(
        None,
        description="逗号分隔 kinds：frequency/region_supervisor/recurrence/gaps；缺省全部分析",
    ),
    factory: str | None = Query(None),
    area: str | None = Query(None),
    categoryL1Id: int | None = Query(None, ge=0),
) -> dict[str, Any]:
    """四分析聚合 + 异常指标。"""
    identity.require("cps_admin")
    ps = _parse_iso(periodStart)
    pe = _parse_iso(periodEnd)
    kinds_list: list[str] | None = None
    if kinds:
        kinds_list = [k.strip() for k in kinds.split(",") if k.strip()]
    filters = SnapshotFilters(
        factory=factory, area=area, category_l1_id=categoryL1Id,
    )
    analysis, _ingestion = services
    try:
        summary = await analysis.summary(ps, pe, filters=filters, kinds=kinds_list)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
        ) from exc
    return summary.to_dict()


@router.post("/snapshot", response_model=SnapshotResponse, status_code=status.HTTP_201_CREATED)
async def snapshot(
    body: dict[str, Any],
    identity: Identity,
    services: ServicesDep,
) -> dict[str, Any]:
    """跑一次 summary → 取最高频 (factory, area, category_l1_id) 顶层 trend → 落快照。

    请求体：``{periodStart, periodEnd, factory?, area?, categoryL1Id?}``。
    """
    identity.require("cps_admin")
    analysis, ingestion = services
    if ingestion is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="postgres_primary 数据源未配置，无法记录快照",
        )
    ps_raw = body.get("periodStart")
    pe_raw = body.get("periodEnd")
    if not ps_raw or not pe_raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="periodStart / periodEnd 必填",
        )
    ps = _parse_iso(str(ps_raw))
    pe = _parse_iso(str(pe_raw))
    filters = SnapshotFilters(
        factory=body.get("factory"),
        area=body.get("area"),
        category_l1_id=body.get("categoryL1Id"),
    )
    summary = await analysis.summary(ps, pe, filters=filters)
    freq = summary.frequency_trends
    if freq:
        top = freq[0]
        snap_factory = top.factory
        snap_area = top.area
        snap_cat = top.category_l1_id
    else:
        snap_factory = filters.factory
        snap_area = filters.area
        snap_cat = filters.category_l1_id
    import uuid as _uuid

    snap = CoverageSnapshot(
        snapshot_id=str(_uuid.uuid4()),
        period_start=ps,
        period_end=pe,
        factory=snap_factory,
        area=snap_area,
        category_l1_id=snap_cat,
        issue_count=summary.summary_metrics.get("total_issues", 0),
        overdue_count=sum(t.overdue_count for t in freq),
        closed_count=sum(t.closed_count for t in freq),
        close_rate=summary.summary_metrics.get("avg_close_rate", 0.0),
        open_count=0,
        handled_count=0,
        recurrence_count=sum(t.recurrence_count for t in freq),
        gap_severity=(
            max((g.gap_severity for g in summary.gaps), default=0)
        ),
        last_record_at=None,
    )
    saved = await ingestion.record_snapshot(snap)
    return {
        "snapshot_id": saved.snapshot_id,
        "period_start": saved.period_start,
        "period_end": saved.period_end,
        "recorded_at": saved.recorded_at,
        "summary_metrics": summary.summary_metrics,
    }


__all__ = ["router"]