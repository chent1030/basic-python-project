"""Effect evaluation API 路由（FR-12）。

挂在 ``api_router``，前缀 ``/api/v1``：

- ``POST /effect/snapshot`` 拉 Java effect → 落 ``memory_metric_snapshot``；
- ``GET  /effect/compare``  跨周期对比；
- ``GET  /effect/regression``  拉 current → 与历史 baseline 比对 → 回归 metric_key 列表。

鉴权：``Identity.require('cps_admin')``（与 memory / procedural 同模式）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.skill.effect_evaluation.application.services import EffectEvaluationService

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/effect", tags=["skill-effect"], route_class=FrameworkRoute)


# ---------------------------------------------------------------- request/response models --


class SnapshotRequest(BaseModel):
    """拉 Java effect → 落 snapshot。"""

    periodStart: str = Field(..., description="ISO 8601 起点")
    periodEnd: str = Field(..., description="ISO 8601 终点")
    scopeKey: str | None = None


# ---------------------------------------------------------------- service locator --


def _effect_service(request: Request) -> EffectEvaluationService:
    cached = getattr(request.app.state, "effect_service", None)
    if cached is not None:
        return cached
    service = EffectEvaluationService()
    request.app.state.effect_service = service
    return service


ServiceDep = Annotated[EffectEvaluationService, Depends(_effect_service)]


# ---------------------------------------------------------------- helpers --


def _validate_iso(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        from fastapi import HTTPException  # noqa: PLC0415

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"非法 ISO 8601 时间: {value!r} ({exc})",
        ) from exc
    return value


# ---------------------------------------------------------------- endpoints --


@router.post("/snapshot", status_code=status.HTTP_202_ACCEPTED)
async def record_snapshot(
    body: SnapshotRequest,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """拉 Java effect → 落 snapshot。"""
    identity.require("cps_admin")
    period_start = _validate_iso(body.periodStart)
    period_end = _validate_iso(body.periodEnd)
    metric = await service.record_current_snapshot(
        period_start=period_start,
        period_end=period_end,
        scope_key=body.scopeKey or "_",
    )
    return {
        "metric_key": metric.metric_key,
        "period_start": metric.period_start,
        "period_end": metric.period_end,
        "scope_key": metric.scope_key,
        "recorded_at": metric.recorded_at,
    }


@router.get("/compare")
async def compare_periods(
    identity: Identity,
    service: ServiceDep,
    baselineStart: str = Query(..., description="ISO 8601 baseline 起点"),
    baselineEnd: str = Query(..., description="ISO 8601 baseline 终点"),
    currentStart: str = Query(..., description="ISO 8601 current 起点"),
    currentEnd: str = Query(..., description="ISO 8601 current 终点"),
    scopeKey: str | None = Query(None, description="scope 标识"),
) -> dict[str, Any]:
    """跨周期对比（list[EffectComparison]）。"""
    identity.require("cps_admin")
    _validate_iso(baselineStart)
    _validate_iso(baselineEnd)
    _validate_iso(currentStart)
    _validate_iso(currentEnd)
    comparisons = await service.compare_periods(
        baselineStart, baselineEnd, currentStart, currentEnd,
        scope_key=scopeKey or "_",
    )
    return {
        "comparisons": [c.to_dict() for c in comparisons],
    }


@router.get("/regression")
async def detect_regression(
    identity: Identity,
    service: ServiceDep,
    periodStart: str = Query(..., description="ISO 8601 current 起点"),
    periodEnd: str = Query(..., description="ISO 8601 current 终点"),
    scopeKey: str | None = Query(None, description="scope 标识"),
    baselineDays: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """检测当前周期相对历史的回归 metric_key 列表。"""
    identity.require("cps_admin")
    _validate_iso(periodStart)
    _validate_iso(periodEnd)
    metric_keys = await service.detect_regression(
        period_start=periodStart,
        period_end=periodEnd,
        scope_key=scopeKey or "_",
        baseline_days=baselineDays,
    )
    return {
        "regression_metric_keys": metric_keys,
        "scope_key": scopeKey or "_",
        "baseline_days": baselineDays,
    }


__all__ = ["router"]