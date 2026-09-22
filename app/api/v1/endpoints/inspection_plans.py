"""C-07 端点（Java → Python）— 巡检计划草稿。

路径：POST /api/v1/agent/inspection-plans/draft（Java CpsAgentFrameworkClient
已配置 base-url=http://127.0.0.1:8000/api/v1，相对路径 /agent/inspection-plans/draft）。

鉴权：内网 → cps_admin 角色（与 C-01/C-03 同源）。
幂等：Java 侧传 idempotency_key=plan-draft-{sourceRunId}，服务端按 key 重放一致。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.projects.inspection_plans.application.service import (
    DraftRequest,
    InspectionPlanDraftService,
)

log = logging.getLogger("cps_agent.inspection_plans")

router = APIRouter(
    prefix="/agent/inspection-plans",
    tags=["agent-inspection-plans"],
    route_class=FrameworkRoute,
)


def _service(request: Request) -> InspectionPlanDraftService:
    svc = getattr(request.app.state, "inspection_plan_draft_service", None)
    if svc is None:
        svc = InspectionPlanDraftService()
        request.app.state.inspection_plan_draft_service = svc
    return svc


ServiceDep = Annotated[InspectionPlanDraftService, Depends(_service)]


def _coerce_str(
    value: Any, field_name: str, *, max_len: int = 256, required: bool = True
) -> str | None:
    if value is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"missing required field: {field_name}",
            )
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be string",
        )
    v = value.strip()
    if not v:
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field_name} cannot be empty",
            )
        return None
    if len(v) > max_len:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} exceeds {max_len} chars",
        )
    return v


@router.post("/draft", response_model=None, status_code=status.HTTP_200_OK)
async def draft_inspection_plan(
    payload: dict[str, Any],
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    identity.require("cps_admin")
    source_run_id = _coerce_str(payload.get("source_run_id"), "source_run_id")
    plan_type = _coerce_str(payload.get("plan_type"), "plan_type", max_len=64)
    title = _coerce_str(payload.get("title"), "title", max_len=512)
    # idempotency_key 可选；缺省服务端派生 plan-draft-{sourceRunId}
    idem = _coerce_str(payload.get("idempotency_key"), "idempotency_key", required=False)
    if not idem:
        idem = f"plan-draft-{source_run_id}"
    factory = _coerce_str(payload.get("factory"), "factory", required=False)
    area = _coerce_str(payload.get("area"), "area", required=False)
    risk_basis = _coerce_str(payload.get("risk_basis"), "risk_basis", required=False, max_len=2048)

    req = DraftRequest(
        idempotency_key=idem,
        source_run_id=source_run_id,
        plan_type=plan_type,
        title=title,
        factory=factory,
        area=area,
        risk_basis=risk_basis,
    )
    result = service.draft(req)
    log.info(
        "[C-07] source_run_id=%s plan_type=%s draft_id=%s status=%s",
        source_run_id, plan_type, result.draft_id, result.status,
    )
    return {
        "draft_id": result.draft_id,
        "plan_type": result.plan_type,
        "title": result.title,
        "source_run_id": result.source_run_id,
        "draft_content_json": result.draft_content_json,
        "generated_at": result.generated_at,
        "model_version": result.model_version,
        "status": result.status,
    }