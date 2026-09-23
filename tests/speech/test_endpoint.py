"""C-06 端点契约测试 — POST /api/v1/agent/speech-to-text。"""

from __future__ import annotations

import base64
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal
from app.api.v1.endpoints.speech import router as speech_router
from app.projects.speech.application.service import SpeechToTextService
from app.projects.speech.infrastructure.asr_client import AsrTranscription, FakeAsrClient
from tests.speech.helpers import FakeAudioFetcher, make_settings

B64 = base64.b64encode(b"fake-audio-bytes").decode("ascii")


def _ok_asr() -> AsrTranscription:
    return AsrTranscription(
        text="泵房地面有积水，需要清理",
        model="qwen3-asr-flash",
        duration_seconds=8.0,
        language="zh",
        raw={"request_id": "asr-1"},
    )


def _build_app(roles: list[str] | None = None, service: Any | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(speech_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600,
    )
    if service is not None:
        app.state.speech_to_text_service = service
    return app


def _make_service(
    *, results: list | None = None, unavailable: str | None = None, settings=None
) -> SpeechToTextService:
    return SpeechToTextService(
        settings=settings or make_settings(),
        asr_client=FakeAsrClient(results=list(results or [_ok_asr()]), unavailable=unavailable),
        audio_fetcher=FakeAudioFetcher(),
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "submission_id": "sub-1",
        "field": "reason",
        "attempt": 1,
        "audio_base64": B64,
    }
    base.update(overrides)
    return base


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- 成功 + 契约形状 ---------------------------------------------------------
def test_transcribe_success_contract_shape():
    app = _build_app(service=_make_service())
    resp = _client(app).post("/api/v1/agent/speech-to-text", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "TRANSCRIBED"
    assert body["text"] == "泵房地面有积水，需要清理"
    assert body["field"] == "reason"
    assert body["confidence"] is None
    assert body["duration_seconds"] == 8.0
    assert body["language"] == "zh"
    assert body["model"] == "qwen3-asr-flash"
    assert body["metadata"]["request_id"] == "asr-1"
    assert body["replayed"] is False
    # 幂等键：缺省派生 speech-{submissionId}-{field}-{attempt}
    assert body["idempotency_key"] == "speech-sub-1-reason-1"
    assert body["request_id"] == body["idempotency_key"]  # C-06 契约别名回显


def test_request_id_alias_accepted_as_idempotency_key():
    app = _build_app(service=_make_service())
    resp = _client(app).post(
        "/api/v1/agent/speech-to-text",
        json=_payload(request_id="speech-sub-1-reason-2", attempt=2),
    )
    assert resp.status_code == 200
    assert resp.json()["idempotency_key"] == "speech-sub-1-reason-2"


def test_idempotent_replay_flagged():
    app = _build_app(service=_make_service())
    client = _client(app)
    first = client.post("/api/v1/agent/speech-to-text", json=_payload()).json()
    second = client.post("/api/v1/agent/speech-to-text", json=_payload()).json()
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["text"] == first["text"]


# --- F2 白名单与入参 --------------------------------------------------------
def test_non_whitelist_field_422():
    app = _build_app(service=_make_service())
    resp = _client(app).post(
        "/api/v1/agent/speech-to-text", json=_payload(field="description")
    )
    assert resp.status_code == 422
    assert "白名单" in str(resp.json()["detail"])


def test_audio_source_xor_violation_422():
    app = _build_app(service=_make_service())
    no_audio = _payload()
    del no_audio["audio_base64"]
    assert (
        _client(app).post("/api/v1/agent/speech-to-text", json=no_audio).status_code
        == 422
    )
    both = _payload(audio_object_key="speech/2026/a.wav")
    assert (
        _client(app).post("/api/v1/agent/speech-to-text", json=both).status_code == 422
    )


def test_audio_format_inferred_from_object_key_suffix():
    service = _make_service()
    app = _build_app(service=service)
    resp = _client(app).post(
        "/api/v1/agent/speech-to-text",
        json=_payload(audio_object_key="speech/2026/req-9.m4a", audio_base64=None),
    )
    assert resp.status_code == 200
    call = service._asr.calls[0]  # noqa: SLF001
    assert call["audio_format"] == "m4a"


# --- SKIPPED 不伪造（A7/B6 对齐）-------------------------------------------
def test_unconfigured_asr_returns_skipped_200():
    app = _build_app(service=_make_service(settings=make_settings(enabled=False)))
    resp = _client(app).post("/api/v1/agent/speech-to-text", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SKIPPED"
    assert body["text"] == ""
    assert "禁用" in body["metadata"]["skip_reason"]


# --- 技术失败 502（不缓存，同键可重试）--------------------------------------
def test_asr_failure_maps_to_502_with_error_code():
    from app.projects.speech.infrastructure.asr_client import AsrCallError

    app = _build_app(service=_make_service(results=[AsrCallError("gateway 500")]))
    resp = _client(app).post("/api/v1/agent/speech-to-text", json=_payload())
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error_code"] == "SPEECH_ASR_FAILED"


# --- 鉴权 -------------------------------------------------------------------
def test_missing_cps_admin_role_403():
    app = _build_app(roles=["cps_employee"], service=_make_service())
    resp = _client(app).post("/api/v1/agent/speech-to-text", json=_payload())
    assert resp.status_code == 403
