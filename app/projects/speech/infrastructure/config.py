"""语音转写配置解析（yaml ``cps_agent.speech`` 节，代码层读取一次冻结）。

- provider 引用 ``llm.providers`` 的 key（取 base_url/api_key，与 chat 模型同源）；
- 本节点 base_url/api_key 非空时覆盖（ASR 专用网关路由）；
- 未配置（provider 不存在 / base_url/api_key 仍为空）→ ``unavailable_reason``
  非空 → 服务层 SKIPPED（不伪造转写，对齐 A7/B6 原则）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class SpeechSettings:
    """F1 ASR 运行时冻结配置快照（已解析 base_url/api_key）。"""

    enabled: bool
    provider: str
    model: str
    base_url: str
    api_key: str
    language: str
    timeout_seconds: float
    max_audio_bytes: int

    @property
    def unavailable_reason(self) -> str | None:
        """前置条件缺失原因（配置完整 → None）。"""
        if not self.enabled:
            return "语音转写已显式禁用（speech.enabled=false）"
        missing = [
            name
            for name, value in (("base_url", self.base_url), ("api_key", self.api_key))
            if not value
        ]
        if missing:
            return (
                f"ASR provider '{self.provider}' 配置不完整（缺少 "
                + "/".join(missing)
                + "；请在 llm.providers 配置该 provider 或在 cps_agent.speech 覆盖）"
            )
        return None


def load_speech_settings() -> SpeechSettings:
    """从全局配置解析（服务构造时读一次；无 env 覆盖项，部署口径即 yaml）。"""
    cfg = settings.cps_agent.speech
    prov = settings.llm.providers.get(cfg.provider)
    return SpeechSettings(
        enabled=cfg.enabled,
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url or (prov.base_url if prov else ""),
        api_key=cfg.api_key or (prov.api_key if prov else ""),
        language=cfg.language.strip(),
        timeout_seconds=cfg.timeout_seconds,
        max_audio_bytes=cfg.max_audio_bytes,
    )


__all__ = [
    "SpeechSettings",
    "load_speech_settings",
]
