"""语音转写配置解析（yaml ``cps_agent.speech`` 节，代码层读取一次冻结）。

- provider 引用 ``llm.providers`` 的 key（取 base_url/api_key，与 chat 模型同源）；
- 本节点 base_url/api_key 非空时覆盖（ASR 专用网关路由）；
- 开关（波次 7 独立）：env ``CPS_SPEECH_ASR_ENABLED`` > ``speech.asr.enabled``
  > 旧 ``speech.enabled`` > 关；见 ``resolve_asr_enabled`` 回退链；
- 未配置（provider 不存在 / base_url/api_key 仍为空）→ ``unavailable_reason``
  非空 → 服务层 SKIPPED（不伪造转写，对齐 A7/B6 原则）。
"""

from __future__ import annotations

import os
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
            return (
                "语音转写已显式禁用（开关链 env CPS_SPEECH_ASR_ENABLED > "
                "cps_agent.speech.asr.enabled > cps_agent.speech.enabled 均关闭/未配置）"
            )
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


#: C-06 ASR 专属 env(波次 7 新增;部署层覆盖 yaml)
ENV_SPEECH_ASR_ENABLED = "CPS_SPEECH_ASR_ENABLED"

_TRUTHY = ("1", "true", "yes", "on")


def resolve_asr_enabled(
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """解析 C-06 ASR 开关(波次 7 独立);返回 ``(enabled, 决策来源说明)``。

    回退链(新开关全域优先于旧开关,同级内 env 优先于 yaml;全链未配 → 关):

    1. env ``CPS_SPEECH_ASR_ENABLED``        —— 新(部署层覆盖)
    2. yaml ``cps_agent.speech.asr.enabled`` —— 新(None=未配)
    3. yaml ``cps_agent.speech.enabled``     —— 旧开关(波次 5-6 唯一入口,兼容)
    4. 以上全未配 → ``False``(端点 SKIPPED 不伪造)

    「老配置仍生效」:只配 ``speech.enabled`` 的既有部署行为不变;显式配置
    新开关后 C-06 与 C-01/C-04 互不影响。
    """
    env = os.environ if env is None else env
    raw = env.get(ENV_SPEECH_ASR_ENABLED)
    if raw is not None:
        return (
            raw.strip().lower() in _TRUTHY,
            f"env {ENV_SPEECH_ASR_ENABLED}={raw}",
        )
    asr = settings.cps_agent.speech.asr.enabled
    if asr is not None:
        return asr, f"cps_agent.speech.asr.enabled={asr}"
    legacy = settings.cps_agent.speech.enabled
    if legacy is not None:
        return legacy, f"回退旧开关 cps_agent.speech.enabled={legacy}"
    return False, "全部开关未配置(默认 false)"


def load_speech_settings(env: dict[str, str] | None = None) -> SpeechSettings:
    """从全局配置解析（服务构造时读一次）。

    enabled 为**已解析**的最终开关（波次 7 起走独立回退链,见
    ``resolve_asr_enabled``）；env ``CPS_SPEECH_ASR_ENABLED`` 优先于 yaml。
    """
    cfg = settings.cps_agent.speech
    prov = settings.llm.providers.get(cfg.provider)
    enabled, _source = resolve_asr_enabled(env)
    return SpeechSettings(
        enabled=enabled,
        provider=cfg.provider,
        model=cfg.model,
        base_url=cfg.base_url or (prov.base_url if prov else ""),
        api_key=cfg.api_key or (prov.api_key if prov else ""),
        language=cfg.language.strip(),
        timeout_seconds=cfg.timeout_seconds,
        max_audio_bytes=cfg.max_audio_bytes,
    )


__all__ = [
    "ENV_SPEECH_ASR_ENABLED",
    "SpeechSettings",
    "load_speech_settings",
    "resolve_asr_enabled",
]
