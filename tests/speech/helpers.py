"""共享测试工厂（speech 包）。"""

from __future__ import annotations

from app.projects.speech.infrastructure.config import SpeechSettings


def make_settings(**overrides: object) -> SpeechSettings:
    """完整可用的 ASR 配置；关键字覆盖单场景（disabled/缺 key/超小限等）。"""
    defaults: dict[str, object] = {
        "enabled": True,
        "provider": "qwen",
        "model": "qwen3-asr-flash",
        "base_url": "http://asr-gw.local/v1",
        "api_key": "test-key",
        "language": "",
        "timeout_seconds": 5.0,
        "max_audio_bytes": 1024,
    }
    defaults.update(overrides)
    return SpeechSettings(**defaults)  # type: ignore[arg-type]


class FakeAudioFetcher:
    """音频取流替身（object_key 路径）。"""

    def __init__(
        self, audio: bytes = b"fake-audio-bytes", fail_with: Exception | None = None
    ) -> None:
        self.audio = audio
        self.fail_with = fail_with
        self.fetched: list[str] = []

    async def fetch_bytes(self, object_key: str) -> bytes:
        if self.fail_with is not None:
            raise self.fail_with
        self.fetched.append(object_key)
        return self.audio
