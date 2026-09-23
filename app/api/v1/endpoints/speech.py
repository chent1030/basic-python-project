"""C-06 端点（Java → Python）— 语音转文字（表单回填辅助）。

路径：POST /api/v1/agent/speech-to-text（Java CpsAgentFrameworkClient
base-url=http://127.0.0.1:8000/api/v1，相对路径 /agent/speech-to-text）。

鉴权：内网 → cps_admin 角色（与 C-04 同源）。
幂等：Java 侧传 idempotency_key=speech-{submissionId}-{field}-{attempt}
（契约别名字段 request_id 同样接受）；服务端按 key 重放一致（replayed=true）。
语义（PRD §20.2 / AC-03 / D-03）：转写文本仅回填表单（原因/短期措施/长期
措施三字段），不作点检证据；敏感词/长度校验属 A5 初审管线，回写后触发。
SKIPPED：ASR 未配置/禁用/鉴权拒绝/RustFS 前置缺失 → 明确跳过不伪造，
Java 引导用户手工输入。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.projects.initial_review.infrastructure.config import (
    load_initial_review_settings,
)
from app.projects.initial_review.infrastructure.model_client import RustFSObjectFetcher
from app.projects.speech.application.service import (
    SpeechRequestError,
    SpeechTechnicalError,
    SpeechToTextService,
    derive_idempotency_key,
)
from app.projects.speech.domain.models import DEFAULT_AUDIO_FORMAT, SpeechTranscribeRequest
from app.projects.speech.infrastructure.asr_client import OpenAiCompatibleAsrClient
from app.projects.speech.infrastructure.config import load_speech_settings

log = logging.getLogger("cps_agent.speech")

router = APIRouter(
    prefix="/agent",
    tags=["agent-speech"],
    route_class=FrameworkRoute,
)

#: base64(10MB) ≈ 13.4M 字符；粗上限防滥用（精确大小在服务层解码后校验）
_MAX_BASE64_CHARS = 20_000_000


def _service(request: Request) -> SpeechToTextService:
    svc = getattr(request.app.state, "speech_to_text_service", None)
    if svc is None:
        cfg = load_speech_settings()
        svc = SpeechToTextService(
            settings=cfg,
            asr_client=OpenAiCompatibleAsrClient(cfg),
            audio_fetcher=RustFSObjectFetcher(load_initial_review_settings().rustfs),
        )
        request.app.state.speech_to_text_service = svc
    return svc


ServiceDep = Annotated[SpeechToTextService, Depends(_service)]


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


def _coerce_attempt(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="attempt must be an integer >= 1",
        )
    return value


def _infer_format(audio_object_key: str | None, explicit: str | None) -> str:
    """audio_format 缺省：显式 > object_key 后缀 > wav。"""
    if explicit:
        return explicit.lstrip(".").lower() or DEFAULT_AUDIO_FORMAT
    if audio_object_key and "." in audio_object_key.rsplit("/", 1)[-1]:
        suffix = audio_object_key.rsplit(".", 1)[-1].lower()
        if suffix.isalnum() and len(suffix) <= 8:
            return suffix
    return DEFAULT_AUDIO_FORMAT


@router.post("/speech-to-text", response_model=None, status_code=status.HTTP_200_OK)
async def speech_to_text(
    payload: dict[str, Any],
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    identity.require("cps_admin")
    submission_id = _coerce_str(payload.get("submission_id"), "submission_id", max_len=64)
    field = _coerce_str(payload.get("field"), "field", max_len=32)
    attempt = _coerce_attempt(payload.get("attempt"))
    audio_object_key = _coerce_str(
        payload.get("audio_object_key"), "audio_object_key", max_len=512, required=False
    )
    audio_base64 = payload.get("audio_base64")
    if audio_base64 is not None:
        if not isinstance(audio_base64, str) or not audio_base64.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="audio_base64 must be a non-empty string",
            )
        if len(audio_base64) > _MAX_BASE64_CHARS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="audio_base64 exceeds limit",
            )
    audio_format = _coerce_str(
        payload.get("audio_format"), "audio_format", max_len=16, required=False
    )
    # idempotency_key 优先；契约别名 request_id 同样接受；缺省按惯例派生
    idem = _coerce_str(payload.get("idempotency_key"), "idempotency_key", required=False)
    if not idem:
        idem = _coerce_str(payload.get("request_id"), "request_id", required=False)
    if not idem:
        idem = derive_idempotency_key(submission_id, field, attempt)

    req = SpeechTranscribeRequest(
        idempotency_key=idem,
        submission_id=submission_id,
        field=field,
        attempt=attempt,
        audio_object_key=audio_object_key,
        audio_base64=audio_base64,
        audio_format=_infer_format(audio_object_key, audio_format),
    )
    try:
        result = await service.transcribe(req)
    except SpeechRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SpeechTechnicalError as exc:
        log.warning("[C-06] speech-to-text technical failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_code": "SPEECH_ASR_FAILED", "message": str(exc)},
        ) from exc
    log.info(
        "[C-06] submission=%s field=%s attempt=%s status=%s replayed=%s",
        submission_id, field, attempt, result.status, result.replayed,
    )
    return {
        "idempotency_key": result.idempotency_key,
        "request_id": result.idempotency_key,
        "submission_id": result.submission_id,
        "field": result.field,
        "attempt": result.attempt,
        "status": result.status,
        "text": result.text,
        "confidence": result.confidence,
        "duration_seconds": result.duration_seconds,
        "language": result.language,
        "model": result.model,
        "audio_object_key": result.audio_object_key,
        "metadata": result.metadata,
        "transcribed_at": result.transcribed_at,
        "replayed": result.replayed,
    }
