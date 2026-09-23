"""ASR 客户端 — OpenAI 兼容 chat/completions ``input_audio`` 同步转写。

选型（DashScope 百炼口径，见 docs/波次5-Python侧-F1F2C06设计.md）：
- ``qwen3-asr-flash``：OpenAI 兼容同步转写，音频 ≤10MB / ≤5min，
  响应 ``choices[0].message.content`` 即转写文本 —— 本实现默认；
- paraformer-v2 / qwen3-asr-flash-filetrans：异步任务 + 公网 URL，
  不适配「同步端点 + 内网 RustFS 取流」场景，不采用；
- 零新增依赖：httpx + stdlib base64（与 RustFS 取流同栈）。

请求最小参数集（兼容 NewAPI 网关转发）：model + messages；
``language`` 配置非空时才附 ``asr_options.language``。

置信度：qwen3-asr-flash 的 OpenAI 兼容响应不提供置信度字段 → ``confidence=None``
（不伪造）；网关若返回顶层 ``confidence`` 则透传。时长取 ``usage.duration``
或 ``usage.seconds``（provider 返回时透传）。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from app.projects.speech.domain.models import audio_mime
from app.projects.speech.infrastructure.config import SpeechSettings

logger = logging.getLogger("cps_agent.speech")

_ERROR_SNIPPET = 300


class AsrUnavailableError(Exception):
    """ASR 前置条件缺失（配置无效/鉴权拒绝）→ 服务层 SKIPPED（不伪造）。"""


class AsrCallError(Exception):
    """ASR 调用技术失败（网络/5xx/超时/响应无效）→ 端点 502，不缓存。"""


@dataclass(frozen=True)
class AsrTranscription:
    """一次成功转写的产物。"""

    text: str
    model: str
    confidence: float | None = None
    duration_seconds: float | None = None
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AsrClient(Protocol):
    """F1 所需最小 ASR 接口（OpenAiCompatibleAsrClient/Fake 均满足）。"""

    async def transcribe(
        self, audio: bytes, *, audio_format: str
    ) -> AsrTranscription: ...


class OpenAiCompatibleAsrClient:
    """OpenAI 兼容 ``POST {base_url}/chat/completions``（input_audio data URI）。

    鉴权失败（401/403）视为配置无效 → AsrUnavailableError（SKIPPED）；
    其余非 2xx / 网络 / 解析失败 → AsrCallError（502 可重试）。
    """

    def __init__(
        self,
        cfg: SpeechSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._transport = transport  # 测试注入 httpx.MockTransport

    def _resolve(self) -> tuple[str, str]:
        reason = self._cfg.unavailable_reason
        if reason:
            raise AsrUnavailableError(reason)
        return self._cfg.base_url.rstrip("/"), self._cfg.api_key

    async def transcribe(
        self, audio: bytes, *, audio_format: str
    ) -> AsrTranscription:
        base_url, api_key = self._resolve()
        data_uri = (
            f"data:{audio_mime(audio_format)};base64,"
            f"{base64.b64encode(audio).decode('ascii')}"
        )
        payload: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_uri}}
                    ],
                }
            ],
            "stream": False,
        }
        if self._cfg.language:
            payload["asr_options"] = {"language": self._cfg.language}

        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self._cfg.timeout_seconds,
                )
        except httpx.HTTPError as exc:
            raise AsrCallError(f"ASR 网络调用失败：{type(exc).__name__}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AsrUnavailableError(
                f"ASR 鉴权失败（HTTP {resp.status_code}），请检查 api_key 配置"
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise AsrCallError(
                f"ASR 网关返回 HTTP {resp.status_code}: {resp.text[:_ERROR_SNIPPET]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise AsrCallError(f"ASR 响应非 JSON：{exc}") from exc
        return self._parse(body)

    def _parse(self, body: dict[str, Any]) -> AsrTranscription:
        """解析 chat.completions 形态的转写响应（宽容取数，不伪造）。"""
        try:
            choices = body.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            content = message.get("content")
            if not isinstance(content, str):
                content = (
                    content.get("text") if isinstance(content, dict) else None
                ) or ""
        except (AttributeError, IndexError, TypeError, KeyError) as exc:
            raise AsrCallError(f"ASR 响应结构无效：{exc}") from exc

        usage = body.get("usage") or {}
        duration: float | None = None
        for key in ("duration", "seconds"):
            raw = usage.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                duration = float(raw)
                break

        confidence: float | None = None
        raw_conf = body.get("confidence")
        if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
            confidence = float(raw_conf)

        language = body.get("language")
        return AsrTranscription(
            text=content.strip(),
            model=str(body.get("model") or self._cfg.model),
            confidence=confidence,
            duration_seconds=duration,
            language=language if isinstance(language, str) else None,
            raw={"request_id": body.get("id")},
        )


class FakeAsrClient:
    """测试替身：按预设脚本返回/抛错（不触网）。"""

    def __init__(
        self,
        *,
        results: list[AsrTranscription | Exception] | None = None,
        unavailable: str | None = None,
    ) -> None:
        self.results = list(results or [])
        self.calls: list[dict[str, Any]] = []
        self.unavailable = unavailable

    async def transcribe(
        self, audio: bytes, *, audio_format: str
    ) -> AsrTranscription:
        self.calls.append({"audio_len": len(audio), "audio_format": audio_format})
        if self.unavailable:
            raise AsrUnavailableError(self.unavailable)
        if not self.results:
            raise AssertionError("FakeAsrClient 预设结果耗尽")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


__all__ = [
    "AsrCallError",
    "AsrClient",
    "AsrTranscription",
    "AsrUnavailableError",
    "FakeAsrClient",
    "OpenAiCompatibleAsrClient",
]
