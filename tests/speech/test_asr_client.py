"""ASR 客户端契约测试 — OpenAI 兼容 chat/completions input_audio。"""

from __future__ import annotations

import json

import httpx
import pytest

from app.projects.speech.infrastructure.asr_client import (
    AsrCallError,
    AsrUnavailableError,
    OpenAiCompatibleAsrClient,
)
from tests.speech.helpers import make_settings


def _ok_body() -> dict:
    return {
        "id": "chatcmpl-asr-1",
        "model": "qwen3-asr-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": " 泵房地面有积水 "}}
        ],
        "usage": {"duration": 12.5},
    }


def _client(handler, **cfg_overrides) -> OpenAiCompatibleAsrClient:
    transport = httpx.MockTransport(handler)
    return OpenAiCompatibleAsrClient(make_settings(**cfg_overrides), transport=transport)


@pytest.mark.asyncio
async def test_transcribe_success_parses_content_and_usage():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_body())

    asr = _client(handler)
    result = await asr.transcribe(b"abc", audio_format="mp3")

    assert captured["url"] == "http://asr-gw.local/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    body = captured["json"]
    assert body["model"] == "qwen3-asr-flash"
    assert body["stream"] is False
    assert "asr_options" not in body  # language 空=自动检测，最小参数集
    (part,) = body["messages"][0]["content"]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["data"].startswith("data:audio/mpeg;base64,")
    # 响应解析
    assert result.text == "泵房地面有积水"  # strip 生效
    assert result.model == "qwen3-asr-flash"
    assert result.duration_seconds == 12.5
    assert result.confidence is None  # OpenAI 兼容口无置信度 → 不伪造
    assert result.raw == {"request_id": "chatcmpl-asr-1"}


@pytest.mark.asyncio
async def test_transcribe_language_config_sends_asr_options():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_body())

    asr = _client(handler, language="zh")
    await asr.transcribe(b"abc", audio_format="wav")
    assert captured["json"]["asr_options"] == {"language": "zh"}


@pytest.mark.asyncio
async def test_transcribe_content_dict_falls_back_to_text_field():
    body = _ok_body()
    body["choices"][0]["message"]["content"] = {"text": "dict content form"}

    asr = _client(lambda req: httpx.Response(200, json=body))
    result = await asr.transcribe(b"abc", audio_format="wav")
    assert result.text == "dict content form"


@pytest.mark.asyncio
async def test_transcribe_confidence_passthrough_when_provider_returns():
    body = _ok_body()
    body["confidence"] = 0.87
    asr = _client(lambda req: httpx.Response(200, json=body))
    result = await asr.transcribe(b"abc", audio_format="wav")
    assert result.confidence == 0.87


@pytest.mark.asyncio
async def test_transcribe_auth_rejected_is_unavailable_not_call_error():
    asr = _client(lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(AsrUnavailableError, match="鉴权失败"):
        await asr.transcribe(b"abc", audio_format="wav")


@pytest.mark.asyncio
async def test_transcribe_gateway_5xx_is_call_error():
    asr = _client(lambda req: httpx.Response(502, text="bad gateway"))
    with pytest.raises(AsrCallError, match="502"):
        await asr.transcribe(b"abc", audio_format="wav")


@pytest.mark.asyncio
async def test_transcribe_non_json_is_call_error():
    asr = _client(lambda req: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(AsrCallError, match="非 JSON"):
        await asr.transcribe(b"abc", audio_format="wav")


@pytest.mark.asyncio
async def test_transcribe_malformed_structure_is_call_error():
    asr = _client(lambda req: httpx.Response(200, json={"choices": [None]}))
    with pytest.raises(AsrCallError, match="ASR 响应结构无效"):
        await asr.transcribe(b"abc", audio_format="wav")


@pytest.mark.asyncio
async def test_transcribe_unconfigured_raises_without_network():
    cfg = make_settings(base_url="", api_key="")
    asr = OpenAiCompatibleAsrClient(cfg)  # transport=None 但不应触网
    with pytest.raises(AsrUnavailableError, match="配置不完整"):
        await asr.transcribe(b"abc", audio_format="wav")
