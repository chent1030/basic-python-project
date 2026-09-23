"""Procedural API 路由（FR-11）。

挂在 ``api_router``，前缀 ``/api/v1``：

- ``POST /procedural/retrieve``  ``DispatchQuery`` → ``list[DispatchHint]``；
- ``POST /procedural/record``   ``DispatchPattern`` → upsert + 返回 pattern_id；
- ``POST /procedural/consolidate``  ``{sinceDays?}`` → ``{merged_count, consolidated_groups}``。

鉴权：``Identity.require('cps_admin')``（与 memory router 同模式）。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.skill.procedural_memory.application.services import ProceduralMemoryService
from app.skill.procedural_memory.domain.models import (
    DispatchPattern,
    DispatchQuery,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/procedural", tags=["skill-procedural"], route_class=FrameworkRoute)


# ---------------------------------------------------------------- request/response models --


class RetrieveRequest(BaseModel):
    """召回 query。"""

    categoryL1Id: int | None = None
    categoryL2Id: int | None = None
    factory: str | None = None
    area: str | None = None
    decision: str | None = None
    topK: int = Field(5, ge=1, le=50)


class RecordRequest(BaseModel):
    """单条 pattern 记录。"""

    categoryL1Id: int
    categoryL2Id: int | None = None
    factory: str | None = None
    area: str
    decision: str
    aiRelation: str
    sampleReasons: list[str] | None = None
    sampleCount: int = Field(1, ge=1, le=10_000)
    patternId: str | None = None


class ConsolidateRequest(BaseModel):
    """合并最近 N 天 pattern。"""

    sinceDays: int = Field(7, ge=1, le=365)


# ---------------------------------------------------------------- service locator --


def _procedural_service(request: Request) -> ProceduralMemoryService:
    cached = getattr(request.app.state, "procedural_service", None)
    if cached is not None:
        return cached
    service = ProceduralMemoryService()
    request.app.state.procedural_service = service
    return service


ServiceDep = Annotated[ProceduralMemoryService, Depends(_procedural_service)]


# ---------------------------------------------------------------- endpoints --


@router.post("/retrieve")
async def retrieve_patterns(
    body: RetrieveRequest,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """召回 dispatch pattern hints。"""
    identity.require("cps_admin")
    scenario = DispatchQuery(
        category_l1_id=body.categoryL1Id,
        category_l2_id=body.categoryL2Id,
        factory=body.factory,
        area=body.area,
        decision=body.decision,
    )
    hints = await service.retrieve_dispatch_patterns(scenario, top_k=body.topK)
    return {"hints": [h.to_dict() for h in hints]}


@router.post("/record", status_code=status.HTTP_202_ACCEPTED)
async def record_pattern(
    body: RecordRequest,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """upsert 一条 dispatch pattern；返回 pattern_id。"""
    identity.require("cps_admin")
    import uuid as _uuid

    pattern_id = body.patternId or str(_uuid.uuid4())
    sample_reasons = list(body.sampleReasons or [])[:5]
    pattern = DispatchPattern(
        pattern_id=pattern_id,
        category_l1_id=body.categoryL1Id,
        category_l2_id=body.categoryL2Id,
        factory=body.factory,
        area=body.area,
        decision=body.decision,
        ai_relation=body.aiRelation,
        sample_count=body.sampleCount,
        sample_reasons=sample_reasons,
    )
    saved = await service.record_pattern(pattern)
    return {"pattern_id": saved.pattern_id, "sample_count": saved.sample_count}


@router.post("/consolidate")
async def consolidate(
    body: ConsolidateRequest,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """合并最近 ``sinceDays`` 天的 pattern（按 category_l1+area+decision 完全一致）。"""
    identity.require("cps_admin")
    if body.sinceDays < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sinceDays 必须 >= 1",
        )
    summary = await service.consolidate_patterns(since_days=body.sinceDays)
    return summary


__all__ = ["router"]