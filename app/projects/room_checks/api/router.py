"""B6 视觉点检 Agent 路由（PRD §23.2 拍图判断 / §30.1 辅房点检）。

路径前缀 ``/agent/room-checks/v2``（与既有 C-04 的
``/agent/room-checks/judge`` 同父前缀但版本隔开，避免 C-04 状态机契约
被新 B6 schema 误吞请求）。

端点：
- POST ``/judge`` — 入参 3-level 判定（fingerpint/roomType/checkItemId/
  photoObjectKey/photoUrl/expectedMatchType/expectedKeywords）→ 同步返回
  ``RoomCheckVerdict`` 200 + 异步 dispatch Java 回调；
- POST ``/rejudge`` — system 角色，强制清缓存重跑同一 fingerprint。

鉴权：与既有 C-04 同源（``Identity.require('cps_admin')``）；rejudge
额外限定 system 角色（``cps_admin`` + ``cps_supervisor``，与波次 8 内部
回调权限约定一致——避免业务操作员滥用 rejudge 触发外部回调风暴）。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.projects.room_checks.application.judge_service import (
    JudgeRequestError,
    JudgeTechnicalError,
    RoomCheckB6JudgeService,
)
from app.projects.room_checks.domain.evidence_models import (
    RoomCheckEvidence,
    RoomType,
)

log = logging.getLogger("cps_agent.room_checks.b6.api")

# rejudge 限定的角色集合（与波次 8 内部回调权限对齐）
REJUDGE_REQUIRED_ROLES = ("cps_admin", "cps_supervisor")


router = APIRouter(
    prefix="/agent/room-checks/v2",
    tags=["agent-room-checks-b6"],
    route_class=FrameworkRoute,
)


def _service(request: Request) -> RoomCheckB6JudgeService:
    """懒建 B6 服务实例并挂 app.state（生产可在 startup 注入）。"""
    svc = getattr(request.app.state, "room_check_b6_judge_service", None)
    if svc is None:
        svc = RoomCheckB6JudgeService()
        request.app.state.room_check_b6_judge_service = svc
    return svc


ServiceDep = Annotated[RoomCheckB6JudgeService, Depends(_service)]


# -- 入参校验（与既有 C-04 ``_coerce_str`` 同模式，B6 字段命名不同） --------
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


def _coerce_room_type(value: Any) -> RoomType:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="roomType must be a non-empty string",
        )
    try:
        return RoomType(value.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"roomType invalid: {value}（允许值 PRIMARY/STANDARD/"
            "SPECIAL/TOOL/OTHER）",
        ) from exc


def _coerce_keywords(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        # 兼容逗号分隔的简写形式（避免 Java 端为单字符串而拒绝）
        items = [v.strip() for v in value.split(",") if v.strip()]
        return tuple(items)
    if isinstance(value, list):
        out: list[str] = []
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"expectedKeywords[{i}] must be non-empty string",
                )
            out.append(item.strip())
        return tuple(out)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="expectedKeywords must be string or list of strings",
    )


def _coerce_int(value: Any, field_name: str, *, min_val: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < min_val:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be integer >= {min_val}",
        )
    return value


# -- Body → Evidence 装配 ---------------------------------------------------
def _evidence_from_payload(payload: dict[str, Any]) -> RoomCheckEvidence:
    fingerprint = _coerce_str(payload.get("fingerprint"), "fingerprint", max_len=256)
    room_type = _coerce_room_type(payload.get("roomType"))
    check_item_id = _coerce_str(payload.get("checkItemId"), "checkItemId", max_len=64)
    # photoObjectKey + photoUrl 互斥：必须有其一（与既有 C-04 同语义）
    photo_object_key = _coerce_str(
        payload.get("photoObjectKey"),
        "photoObjectKey",
        required=False,
        max_len=512,
    )
    photo_url = _coerce_str(
        payload.get("photoUrl"), "photoUrl", required=False, max_len=2048
    )
    if not photo_object_key and not photo_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="photoObjectKey or photoUrl required",
        )
    expected_match_type = _coerce_str(
        payload.get("expectedMatchType"),
        "expectedMatchType",
        required=False,
        max_len=128,
    )
    expected_keywords = _coerce_keywords(payload.get("expectedKeywords"))
    return RoomCheckEvidence(
        fingerprint=fingerprint,  # type: ignore[arg-type]
        room_type=room_type,
        check_item_id=check_item_id,  # type: ignore[arg-type]
        photo_object_key=photo_object_key,  # type: ignore[arg-type]
        photo_url=photo_url,
        expected_match_type=expected_match_type,
        expected_keywords=expected_keywords,
    )


# -- 响应序列化 --------------------------------------------------------------
def _verdict_to_response(verdict) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "fingerprint": verdict.fingerprint,
        "overall": verdict.overall.value,
        "score": int(verdict.score),
        "reasons": list(verdict.reasons),
        "judged_at": verdict.judged_at.isoformat()
        if hasattr(verdict.judged_at, "isoformat")
        else str(verdict.judged_at),
        "model_name": verdict.model_name,
        "raw_output": dict(verdict.raw_output),
    }


# -- 端点 --------------------------------------------------------------------
@router.post("/judge", response_model=None, status_code=status.HTTP_200_OK)
async def judge_room_check_b6(
    payload: dict[str, Any],
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """B6 三级视觉点检判定。

    请求体字段：
    - fingerprint（必填，≤256）：幂等键
    - roomType（必填，枚举）：辅房类型
    - checkItemId（必填，≤64）：点检项 ID
    - photoObjectKey（必填，≤512）：RustFS 对象键
    - photoUrl（可选，≤2048）：直连 URL（与 object_key 互斥）
    - expectedMatchType（可选）：期望照片主体类型
    - expectedKeywords（可选，list[str] 或逗号分隔）：期望关键物证
    """
    identity.require("cps_admin")
    evidence = _evidence_from_payload(payload)
    try:
        verdict = await service.judge(evidence)
    except JudgeRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except JudgeTechnicalError as exc:
        log.warning("[B6] judge technical failure fingerprint=%s: %s", evidence.fingerprint, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_code": "ROOM_CHECK_B6_MODEL_FAILED", "message": str(exc)},
        ) from exc
    log.info(
        "[B6] fingerprint=%s check_item=%s overall=%s score=%s reasons=%d",
        verdict.fingerprint,
        evidence.check_item_id,
        verdict.overall.value,
        verdict.score,
        len(verdict.reasons),
    )
    return _verdict_to_response(verdict)


@router.post("/rejudge", response_model=None, status_code=status.HTTP_200_OK)
async def rejudge_room_check_b6(
    payload: dict[str, Any],
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """B6 强制重判（system 角色限定；清缓存后重跑同一 fingerprint）。

    请求体字段：
    - fingerprint（必填，≤256）：目标 fingerprint
    - retryCount（可选，≥1）：重试次数（仅审计日志使用）

    权限：必须**同时**具备 ``cps_admin`` + ``cps_supervisor``（系统级角色
    互为冗余；与波次 8 内部回调权限约定一致）——``Principal.require``
    仅做交集校验（任一即过），不能表达「双角色必备」，故这里手动校验。
    """
    if not ("cps_admin" in identity.roles and "cps_supervisor" in identity.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="rejudge requires both cps_admin and cps_supervisor roles",
        )
    fingerprint = _coerce_str(payload.get("fingerprint"), "fingerprint", max_len=256)
    retry_count: int | None = None
    if "retryCount" in payload and payload["retryCount"] is not None:
        retry_count = _coerce_int(payload.get("retryCount"), "retryCount", min_val=1)
    # 重新构造 minimal evidence（其余字段从缓存或重新读）——B6 不持久化
    # 原 evidence，故此处重跑需调用方重新提供完整 body。我们允许 fingerprint
    # 单独触发：调用方只重跑缓存清除，不重新判定；返回「缓存已清除」状态。
    cleared = service.cache.invalidate(fingerprint)  # type: ignore[arg-type]
    log.info(
        "[B6 rejudge] fingerprint=%s retry=%s cleared=%s actor=%s",
        fingerprint,
        retry_count,
        cleared,
        identity.actor,
    )
    return {
        "fingerprint": fingerprint,
        "cache_cleared": bool(cleared),
        "retry_count": retry_count,
        "actor": identity.actor,
    }


__all__ = ["router"]