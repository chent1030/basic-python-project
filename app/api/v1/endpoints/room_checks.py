"""C-04 端点（Java → Python）— 辅房点检视觉判定。

路径：POST /api/v1/agent/room-checks/judge（Java CpsAgentFrameworkClient
已配置 base-url=http://127.0.0.1:8000/api/v1，相对路径 /agent/room-checks/judge）。

鉴权：内网 → cps_admin 角色（与 C-01/C-03/C-07 同源）。
幂等：Java 侧传 idempotency_key=room-judge-{submissionId}-{itemId}-{attempt}，
服务端按 key 重放一致（replayed=true 标识）；缺省按契约格式派生。
同步调用（现场等待）：业务结果 200；技术失败 502（不缓存，同键可重试）。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.projects.room_checks.application.service import (
    JudgeRequestError,
    JudgeTechnicalError,
    RoomCheckJudgeService,
    derive_idempotency_key,
)
from app.projects.room_checks.domain.models import ENDPOINT_MAX_PHOTOS, JudgeRequest

log = logging.getLogger("cps_agent.room_checks")

router = APIRouter(
    prefix="/agent/room-checks",
    tags=["agent-room-checks"],
    route_class=FrameworkRoute,
)


def _service(request: Request) -> RoomCheckJudgeService:
    svc = getattr(request.app.state, "room_check_judge_service", None)
    if svc is None:
        svc = RoomCheckJudgeService()
        request.app.state.room_check_judge_service = svc
    return svc


ServiceDep = Annotated[RoomCheckJudgeService, Depends(_service)]


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


def _coerce_photos(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="photo_object_keys must be a non-empty list",
        )
    if len(value) > ENDPOINT_MAX_PHOTOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"photo_object_keys exceeds {ENDPOINT_MAX_PHOTOS} items",
        )
    keys: list[str] = []
    for i, item in enumerate(value):
        key = _coerce_str(item, f"photo_object_keys[{i}]", max_len=512)
        keys.append(key)  # type: ignore[arg-type]  # _coerce_str 必返 str（required）
    return keys


def _coerce_attempt(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="attempt must be an integer >= 1",
        )
    return value


def _coerce_deduction(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="deduction must be a number",
        )
    return float(value)


@router.post("/judge", response_model=None, status_code=status.HTTP_200_OK)
async def judge_room_check(
    payload: dict[str, Any],
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    identity.require("cps_admin")
    submission_id = _coerce_str(payload.get("submission_id"), "submission_id", max_len=64)
    item_id = _coerce_str(payload.get("item_id"), "item_id", max_len=64)
    attempt = _coerce_attempt(payload.get("attempt"))
    item_content = _coerce_str(payload.get("item_content"), "item_content", max_len=512)
    item_type = _coerce_str(payload.get("item_type"), "item_type", max_len=64)
    photos = _coerce_photos(payload.get("photo_object_keys"))
    deduction = _coerce_deduction(payload.get("deduction"))
    config_version = _coerce_str(
        payload.get("config_version"), "config_version", max_len=64, required=False
    )
    room_name = _coerce_str(payload.get("room_name"), "room_name", required=False)
    # idempotency_key 可选；缺省按契约派生 room-judge-{submissionId}-{itemId}-{attempt}
    idem = _coerce_str(payload.get("idempotency_key"), "idempotency_key", required=False)
    if not idem:
        idem = derive_idempotency_key(submission_id, item_id, attempt)

    req = JudgeRequest(
        idempotency_key=idem,
        submission_id=submission_id,
        item_id=item_id,
        attempt=attempt,
        item_content=item_content,
        item_type=item_type,
        photo_object_keys=tuple(photos),
        deduction=deduction,
        config_version=config_version,
        room_name=room_name,
    )
    try:
        result = await service.judge(req)
    except JudgeRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except JudgeTechnicalError as exc:
        log.warning("[C-04] judge technical failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_code": "ROOM_JUDGE_MODEL_FAILED", "message": str(exc)},
        ) from exc
    log.info(
        "[C-04] submission=%s item=%s attempt=%s status=%s verdict=%s replayed=%s",
        submission_id, item_id, attempt, result.status, result.verdict, result.replayed,
    )
    return {
        "idempotency_key": result.idempotency_key,
        "submission_id": result.submission_id,
        "item_id": result.item_id,
        "attempt": result.attempt,
        "status": result.status,
        "verdict": result.verdict,
        "reason": result.reason,
        "evidence": result.evidence,
        "photo_object_keys": list(result.photo_object_keys),
        "item_snapshot": result.item_snapshot,
        "stage_trace": result.stage_trace,
        "model_version": result.model_version,
        "prompt_versions": result.prompt_versions,
        "judged_at": result.judged_at,
        "replayed": result.replayed,
    }
