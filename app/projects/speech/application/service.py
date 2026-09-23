"""F1 语音转写应用服务（C-06 speech-to-text）。

职责与口径：
- 三字段白名单（D-03）：``reason / short_term / long_term``，非白名单 422；
- 音频来源二选一（互斥）：``audio_object_key``（RustFS 取流）或
  ``audio_base64``（内嵌），两者都给/都不给 → 422；
- 幂等：键惯例 ``speech-{submissionId}-{field}-{attempt}``（服务端可派生）；
  进程内 LRU+TTL 缓存，命中 → 同结果 + ``replayed=true``（SKIPPED 可重放）；
- 不伪造：ASR 未配置/禁用/鉴权拒绝/RustFS 配置缺失 → ``SKIPPED``（缓存可重放）；
- 技术失败：ASR 网络/网关/超时/响应无效 → ``SpeechTechnicalError``（端点 502，
  不缓存，Java 同幂等键可重试）；
- 转写结果**仅回填表单**（PRD §20.2 / AC-03），敏感词与长度校验属 A5 初审
  管线职责，回写后自然触发，本服务不做。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    ModelUnavailableError,
)
from app.projects.speech.domain.models import (
    FIELD_LABELS,
    SPEECH_MODEL_VERSION,
    STATUS_SKIPPED,
    STATUS_TRANSCRIBED,
    TARGET_FIELDS,
    SpeechTranscribeRequest,
    SpeechTranscribeResult,
)
from app.projects.speech.infrastructure.asr_client import (
    AsrCallError,
    AsrUnavailableError,
)
from app.projects.speech.infrastructure.config import SpeechSettings

logger = logging.getLogger("cps_agent.speech")

_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_MAX_ENTRIES = 1024


class SpeechRequestError(Exception):
    """入参非法（非白名单字段/音频来源二选一违约/base64 无效/超限）→ 422。"""


class SpeechTechnicalError(Exception):
    """技术失败（ASR 网络/网关/超时/RustFS 取流失败）→ 502，不缓存可重试。"""


@runtime_checkable
class AudioFetcher(Protocol):
    """音频取流最小接口（initial_review 的 RustFSObjectFetcher 满足）。"""

    async def fetch_bytes(self, object_key: str) -> bytes: ...


def derive_idempotency_key(submission_id: str, field: str, attempt: int) -> str:
    """幂等键惯例（与 Java 侧同源生成；缺省时服务端按此派生）。"""
    return f"speech-{submission_id}-{field}-{attempt}"


class _ResultCache:
    """进程内幂等缓存（LRU + TTL，与 room_checks 同模式）。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = _CACHE_TTL_SECONDS,
        max_entries: int = _CACHE_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, SpeechTranscribeResult]] = (
            OrderedDict()
        )

    def get(self, key: str) -> SpeechTranscribeResult | None:
        now = time.monotonic()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            stored_at, result = item
            if now - stored_at > self._ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return result

    def put(self, key: str, result: SpeechTranscribeResult) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)


class SpeechToTextService:
    """C-06 语音转写（表单回填辅助，非点检证据）。"""

    def __init__(
        self,
        *,
        settings: SpeechSettings,
        asr_client: object,
        audio_fetcher: AudioFetcher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = settings
        self._asr = asr_client
        self._fetcher = audio_fetcher
        self._now = now or (lambda: datetime.now(UTC))
        self._cache = _ResultCache()

    async def transcribe(self, req: SpeechTranscribeRequest) -> SpeechTranscribeResult:
        self._validate(req)
        key = req.idempotency_key or derive_idempotency_key(
            req.submission_id, req.field, req.attempt
        )
        req = replace(req, idempotency_key=key)

        cached = self._cache.get(key)
        if cached is not None:
            logger.info("speech-to-text 缓存命中 key=%s status=%s", key, cached.status)
            return replace(cached, replayed=True)

        # ASR 前置检查先行：未配置/禁用则无需取音频，直接 SKIPPED（可重放）
        reason = self._cfg.unavailable_reason
        if reason:
            return self._finish_skipped(req, reason)

        try:
            audio = await self._load_audio(req)
        except _SkipNow as exc:
            # RustFS 前置缺失：SKIPPED 结果已入缓存，此处返回
            return self._skipped(req, str(exc))

        try:
            async with asyncio.timeout(self._cfg.timeout_seconds):
                asr = await self._asr.transcribe(audio, audio_format=req.audio_format)
        except AsrUnavailableError as exc:
            # 鉴权拒绝等配置级不可用 → SKIPPED（不伪造，缓存可重放）
            return self._finish_skipped(req, str(exc))
        except (AsrCallError, TimeoutError) as exc:
            raise SpeechTechnicalError(
                f"ASR 转写失败（key={key}）：{type(exc).__name__}: {exc}"
            ) from exc

        result = SpeechTranscribeResult(
            idempotency_key=key,
            submission_id=req.submission_id,
            field=req.field,
            attempt=req.attempt,
            status=STATUS_TRANSCRIBED,
            text=asr.text,
            confidence=asr.confidence,
            duration_seconds=asr.duration_seconds,
            language=asr.language,
            model=asr.model or self._cfg.model,
            audio_object_key=req.audio_object_key,
            metadata={"model_version": SPEECH_MODEL_VERSION, **asr.raw},
            transcribed_at=self._now().isoformat(),
        )
        self._cache.put(key, result)
        logger.info(
            "speech-to-text 完成 key=%s field=%s text_len=%d",
            key,
            req.field,
            len(asr.text),
        )
        return result

    # --- 入参校验 -----------------------------------------------------------
    @staticmethod
    def _validate(req: SpeechTranscribeRequest) -> None:
        if req.field not in TARGET_FIELDS:
            allowed = "、".join(f"{f}({FIELD_LABELS[f]})" for f in TARGET_FIELDS)
            raise SpeechRequestError(
                f"field 非法：'{req.field}'；白名单（D-03 三字段）：{allowed}"
            )
        if bool(req.audio_object_key) == bool(req.audio_base64):
            raise SpeechRequestError(
                "audio_object_key 与 audio_base64 必须二选一（恰好提供一个）"
            )
        if req.attempt < 1:
            raise SpeechRequestError(f"attempt 必须 ≥1，收到 {req.attempt}")

    async def _load_audio(self, req: SpeechTranscribeRequest) -> bytes:
        """取音频字节：object_key → RustFS 内网取流；base64 → 解码校验。"""
        limit = self._cfg.max_audio_bytes
        if req.audio_object_key:
            try:
                audio = await self._fetcher.fetch_bytes(req.audio_object_key)
            except ModelUnavailableError as exc:
                # RustFS 配置缺失等前置不具备 → SKIPPED（不伪造，可重放）
                self._cache.put(req.idempotency_key, self._skipped(req, str(exc)))
                raise _SkipNow(str(exc)) from exc
            except ModelCallError as exc:
                raise SpeechTechnicalError(f"音频对象获取失败：{exc}") from exc
            if len(audio) > limit:
                raise SpeechRequestError(
                    f"音频超限：{len(audio)} 字节 > 上限 {limit} 字节（10MB）"
                )
            return audio
        try:
            raw = base64.b64decode(req.audio_base64 or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SpeechRequestError(f"audio_base64 无效：{exc}") from exc
        if len(raw) > limit:
            raise SpeechRequestError(
                f"音频超限：{len(raw)} 字节 > 上限 {limit} 字节（10MB）"
            )
        return raw

    def _finish_skipped(
        self, req: SpeechTranscribeRequest, reason: str
    ) -> SpeechTranscribeResult:
        result = self._skipped(req, reason)
        self._cache.put(req.idempotency_key, result)
        logger.warning(
            "speech-to-text SKIPPED key=%s reason=%s", req.idempotency_key, reason
        )
        return result

    def _skipped(self, req: SpeechTranscribeRequest, reason: str) -> SpeechTranscribeResult:
        return SpeechTranscribeResult(
            idempotency_key=req.idempotency_key,
            submission_id=req.submission_id,
            field=req.field,
            attempt=req.attempt,
            status=STATUS_SKIPPED,
            audio_object_key=req.audio_object_key,
            metadata={"skip_reason": reason, "model_version": SPEECH_MODEL_VERSION},
            transcribed_at=self._now().isoformat(),
        )


class _SkipNow(Exception):
    """内部信号：SKIPPED 结果已入缓存，transcribe 内捕获后原样返回。"""
