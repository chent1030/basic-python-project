"""SpeechToTextService 契约测试 — 白名单/二选一/幂等/SKIPPED 不伪造/502 分类。"""

from __future__ import annotations

import base64
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    ModelUnavailableError,
)
from app.projects.speech.application.service import (
    SpeechRequestError,
    SpeechTechnicalError,
    SpeechToTextService,
    derive_idempotency_key,
)
from app.projects.speech.domain.models import (
    STATUS_SKIPPED,
    STATUS_TRANSCRIBED,
    SpeechTranscribeRequest,
)
from app.projects.speech.infrastructure.asr_client import (
    AsrCallError,
    AsrTranscription,
    FakeAsrClient,
)
from tests.speech.helpers import FakeAudioFetcher, make_settings

B64 = base64.b64encode(b"fake-audio-bytes").decode("ascii")


def _req(**overrides: object) -> SpeechTranscribeRequest:
    defaults: dict[str, object] = {
        "idempotency_key": "",
        "submission_id": "sub-1",
        "field": "reason",
        "attempt": 1,
        "audio_base64": B64,
    }
    defaults.update(overrides)
    return SpeechTranscribeRequest(**defaults)  # type: ignore[arg-type]


def _service(
    *,
    settings=None,
    results: list | None = None,
    unavailable: str | None = None,
    fetcher=None,
) -> SpeechToTextService:
    return SpeechToTextService(
        settings=settings or make_settings(),
        asr_client=FakeAsrClient(results=results, unavailable=unavailable),
        audio_fetcher=fetcher or FakeAudioFetcher(),
    )


def _ok_asr() -> AsrTranscription:
    return AsrTranscription(
        text="泵房地面有积水，需要清理",
        model="qwen3-asr-flash",
        duration_seconds=8.0,
        raw={"request_id": "asr-1"},
    )


# --- 入参校验（F2 三字段白名单 + 音频二选一） -------------------------------
@pytest.mark.asyncio
async def test_non_whitelist_field_rejected():
    svc = _service()
    for bad in ("description", "photo_note", "REASON", ""):
        with pytest.raises(SpeechRequestError, match="白名单|field 非法"):
            await svc.transcribe(_req(field=bad, audio_base64=B64))


@pytest.mark.asyncio
async def test_whitelist_fields_accepted():
    for field in ("reason", "short_term", "long_term"):
        svc = _service(results=[_ok_asr()])
        result = await svc.transcribe(_req(field=field))
        assert result.status == STATUS_TRANSCRIBED


@pytest.mark.asyncio
async def test_audio_source_must_be_exactly_one():
    svc = _service()
    with pytest.raises(SpeechRequestError, match="二选一"):
        await svc.transcribe(_req(audio_object_key=None, audio_base64=None))
    with pytest.raises(SpeechRequestError, match="二选一"):
        await svc.transcribe(_req(audio_object_key="speech/2026/sub-1.wav"))


@pytest.mark.asyncio
async def test_invalid_base64_rejected():
    svc = _service()
    with pytest.raises(SpeechRequestError, match="audio_base64 无效"):
        await svc.transcribe(_req(audio_base64="!!!not-base64!!!"))


@pytest.mark.asyncio
async def test_oversize_audio_rejected():
    svc = _service(settings=make_settings(max_audio_bytes=8))
    with pytest.raises(SpeechRequestError, match="超限"):
        await svc.transcribe(_req())  # 解码后 16 字节 > 8


@pytest.mark.asyncio
async def test_attempt_must_be_positive():
    svc = _service()
    with pytest.raises(SpeechRequestError, match="attempt"):
        await svc.transcribe(_req(attempt=0))


# --- 成功路径 + 幂等 ---------------------------------------------------------
@pytest.mark.asyncio
async def test_transcribe_success_and_idempotent_replay():
    fixed = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    svc = _service(
        results=[_ok_asr()],
        settings=make_settings(),
    )
    svc._now = lambda: fixed  # noqa: SLF001 — 测试注入固定时钟
    req = _req(idempotency_key="speech-sub-1-reason-1")

    first = await svc.transcribe(req)
    assert first.status == STATUS_TRANSCRIBED
    assert first.text == "泵房地面有积水，需要清理"
    assert first.model == "qwen3-asr-flash"
    assert first.duration_seconds == 8.0
    assert first.confidence is None
    assert first.metadata["request_id"] == "asr-1"
    assert first.metadata["model_version"] == "speech/asr-openai-compat@1"
    assert first.transcribed_at == fixed.isoformat()
    assert first.replayed is False

    second = await svc.transcribe(req)
    assert second == replace(first, replayed=True)
    assert second.replayed is True
    assert second.text == first.text


@pytest.mark.asyncio
async def test_derived_idempotency_key_convention():
    svc = _service(results=[_ok_asr()])
    result = await svc.transcribe(_req())  # 未传 key
    assert result.idempotency_key == "speech-sub-1-reason-1"
    assert derive_idempotency_key("s", "short_term", 2) == "speech-s-short_term-2"


# --- SKIPPED 不伪造 ---------------------------------------------------------
@pytest.mark.asyncio
async def test_unconfigured_asr_skips_without_faking():
    svc = _service(settings=make_settings(enabled=False))
    result = await svc.transcribe(_req())
    assert result.status == STATUS_SKIPPED
    assert result.text == ""  # 不伪造转写
    assert "禁用" in result.metadata["skip_reason"]
    # SKIPPED 也缓存可重放
    replay = await svc.transcribe(_req())
    assert replay.replayed is True


@pytest.mark.asyncio
async def test_missing_provider_config_skips():
    svc = _service(settings=make_settings(base_url="", api_key=""))
    result = await svc.transcribe(_req())
    assert result.status == STATUS_SKIPPED
    assert "配置不完整" in result.metadata["skip_reason"]


@pytest.mark.asyncio
async def test_asr_auth_rejected_skips_and_caches():
    svc = _service(unavailable="ASR 鉴权失败（HTTP 401）")
    first = await svc.transcribe(_req(idempotency_key="k-auth"))
    assert first.status == STATUS_SKIPPED
    assert "401" in first.metadata["skip_reason"]
    replay = await svc.transcribe(_req(idempotency_key="k-auth"))
    assert replay.replayed is True


@pytest.mark.asyncio
async def test_rustfs_config_missing_skips():
    fetcher = FakeAudioFetcher(fail_with=ModelUnavailableError("RustFS 配置不完整"))
    svc = _service(fetcher=fetcher)
    result = await svc.transcribe(
        _req(audio_object_key="speech/2026/a.wav", audio_base64=None)
    )
    assert result.status == STATUS_SKIPPED
    assert "RustFS" in result.metadata["skip_reason"]


# --- 技术失败 → 502 不缓存 ---------------------------------------------------
@pytest.mark.asyncio
async def test_asr_call_error_is_technical_and_not_cached():
    svc = _service(results=[AsrCallError("gateway 500")])
    with pytest.raises(SpeechTechnicalError, match="ASR 转写失败"):
        await svc.transcribe(_req(idempotency_key="k-502"))
    # 失败不缓存：同键再次调用仍走客户端（此处给出成功结果应正常返回）
    svc._asr.results.append(_ok_asr())  # noqa: SLF001
    result = await svc.transcribe(_req(idempotency_key="k-502"))
    assert result.status == STATUS_TRANSCRIBED


@pytest.mark.asyncio
async def test_rustfs_fetch_failure_is_technical():
    fetcher = FakeAudioFetcher(fail_with=ModelCallError("S3 503"))
    svc = _service(fetcher=fetcher)
    with pytest.raises(SpeechTechnicalError, match="音频对象获取失败"):
        await svc.transcribe(
            _req(audio_object_key="speech/2026/a.wav", audio_base64=None)
        )


# --- object_key 路径 ---------------------------------------------------------
@pytest.mark.asyncio
async def test_object_key_fetch_uses_rustfs_and_format_passthrough():
    fetcher = FakeAudioFetcher(audio=b"object-audio")
    svc = _service(results=[_ok_asr()], fetcher=fetcher)
    result = await svc.transcribe(
        _req(audio_object_key="speech/2026/req-1.m4a", audio_base64=None, audio_format="m4a")
    )
    assert fetcher.fetched == ["speech/2026/req-1.m4a"]
    assert result.audio_object_key == "speech/2026/req-1.m4a"
    asr_call = svc._asr.calls[0]  # noqa: SLF001
    assert asr_call["audio_len"] == len(b"object-audio")
    assert asr_call["audio_format"] == "m4a"


@pytest.mark.asyncio
async def test_object_key_oversize_rejected():
    fetcher = FakeAudioFetcher(audio=b"x" * 2048)
    svc = _service(settings=make_settings(max_audio_bytes=1024), fetcher=fetcher)
    with pytest.raises(SpeechRequestError, match="超限"):
        await svc.transcribe(
            _req(audio_object_key="speech/2026/big.wav", audio_base64=None)
        )


def test_cache_ttl_expiry():
    """TTL 过期后缓存失效（用注入时钟驱动 monotonic 不可行，直接操纵缓存）。"""
    import app.projects.speech.application.service as svc_mod

    cache = svc_mod._ResultCache(ttl_seconds=0.01)
    from app.projects.speech.domain.models import SpeechTranscribeResult

    result = SpeechTranscribeResult(
        idempotency_key="k", submission_id="s", field="reason",
        attempt=1, status=STATUS_TRANSCRIBED, text="t",
    )
    cache.put("k", result)
    assert cache.get("k") is not None
    time.sleep(0.02)
    assert cache.get("k") is None
