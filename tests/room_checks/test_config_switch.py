"""波次 7 独立模型开关解析测试(清单⑥)。

覆盖 C-04 视觉判定(``resolve_vision_enabled``)与 C-06 ASR
(``resolve_asr_enabled``)两条回退链:新开关(env/yaml)优先于旧开关
(env/yaml),全链未配默认 false(SKIPPED 不伪造),老配置仍生效。
"""

from __future__ import annotations

import pytest

from app.core.config import (
    AsrSwitchConfig,
    CpsAgentConfig,
    InitialReviewConfig,
    ModelCheckConfig,
    RoomChecksConfig,
    RoomChecksVisionConfig,
    SpeechConfig,
    settings,
)
from app.projects.room_checks.infrastructure.config import (
    ENV_LEGACY_MODEL_CHECK_ENABLED,
    ENV_ROOM_CHECKS_VISION_ENABLED,
    resolve_vision_enabled,
)
from app.projects.speech.infrastructure.config import (
    ENV_SPEECH_ASR_ENABLED,
    resolve_asr_enabled,
)


def _swap_cps_agent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_check_enabled: bool | None = None,
    vision_enabled: bool | None = None,
    speech_enabled: bool | None = None,
    asr_enabled: bool | None = None,
) -> None:
    """替换全局 settings.cps_agent(monkeypatch 自动恢复,测试间无泄漏)。"""
    monkeypatch.setattr(
        settings,
        "cps_agent",
        CpsAgentConfig(
            initial_review=InitialReviewConfig(
                model_check=ModelCheckConfig(enabled=model_check_enabled)
            ),
            room_checks=RoomChecksConfig(
                vision=RoomChecksVisionConfig(enabled=vision_enabled)
            ),
            speech=SpeechConfig(
                enabled=speech_enabled, asr=AsrSwitchConfig(enabled=asr_enabled)
            ),
        ),
    )


# --- C-04 视觉判定开关回退链 ----------------------------------------------------


def test_vision_all_unset_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """全链未配 → False(默认关,SKIPPED 不伪造)。"""
    _swap_cps_agent(monkeypatch)
    enabled, source = resolve_vision_enabled(env={})
    assert enabled is False
    assert "未配置" in source


def test_vision_legacy_yaml_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """老配置仍生效:仅配 initial_review.model_check.enabled → C-04 跟随。"""
    _swap_cps_agent(monkeypatch, model_check_enabled=True)
    enabled, source = resolve_vision_enabled(env={})
    assert enabled is True and "model_check" in source

    _swap_cps_agent(monkeypatch, model_check_enabled=False)
    enabled, _ = resolve_vision_enabled(env={})
    assert enabled is False


def test_vision_new_yaml_overrides_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """独立新开关显式配置后与 C-01 解耦(双向)。"""
    _swap_cps_agent(monkeypatch, model_check_enabled=True, vision_enabled=False)
    enabled, source = resolve_vision_enabled(env={})
    assert enabled is False and "room_checks.vision" in source

    _swap_cps_agent(monkeypatch, model_check_enabled=False, vision_enabled=True)
    enabled, _ = resolve_vision_enabled(env={})
    assert enabled is True


def test_vision_new_env_beats_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """新 env 最高优先:覆盖新 yaml 与旧 env/yaml。"""
    _swap_cps_agent(monkeypatch, model_check_enabled=True, vision_enabled=False)
    enabled, source = resolve_vision_enabled(
        env={ENV_ROOM_CHECKS_VISION_ENABLED: "true",
             ENV_LEGACY_MODEL_CHECK_ENABLED: "false"}
    )
    assert enabled is True and ENV_ROOM_CHECKS_VISION_ENABLED in source


def test_vision_legacy_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧共享 env 兼容:新开关未配时仍生效(波次 4-6 部署口径)。"""
    _swap_cps_agent(monkeypatch, model_check_enabled=True)
    enabled, source = resolve_vision_enabled(
        env={ENV_LEGACY_MODEL_CHECK_ENABLED: "false"}
    )
    assert enabled is False and ENV_LEGACY_MODEL_CHECK_ENABLED in source


# --- C-06 ASR 开关回退链 --------------------------------------------------------


def test_asr_all_unset_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _swap_cps_agent(monkeypatch)
    enabled, source = resolve_asr_enabled(env={})
    assert enabled is False and "未配置" in source


def test_asr_legacy_speech_enabled_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """老配置仍生效:仅配 speech.enabled(波次 5-6 口径)→ ASR 跟随。"""
    _swap_cps_agent(monkeypatch, speech_enabled=True)
    enabled, source = resolve_asr_enabled(env={})
    assert enabled is True and "speech.enabled" in source

    _swap_cps_agent(monkeypatch, speech_enabled=False)
    enabled, _ = resolve_asr_enabled(env={})
    assert enabled is False


def test_asr_new_switch_overrides_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """独立新开关 speech.asr.enabled 显式配置后与旧开关解耦(双向)。"""
    _swap_cps_agent(monkeypatch, speech_enabled=True, asr_enabled=False)
    enabled, source = resolve_asr_enabled(env={})
    assert enabled is False and "asr.enabled" in source

    _swap_cps_agent(monkeypatch, speech_enabled=False, asr_enabled=True)
    enabled, _ = resolve_asr_enabled(env={})
    assert enabled is True


def test_asr_env_beats_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    _swap_cps_agent(monkeypatch, speech_enabled=True, asr_enabled=True)
    enabled, source = resolve_asr_enabled(env={ENV_SPEECH_ASR_ENABLED: "false"})
    assert enabled is False and ENV_SPEECH_ASR_ENABLED in source


# --- C-01 不受新开关影响(initial_review 自身默认契约) ----------------------------


def test_initial_review_default_true_when_model_check_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """model_check.enabled 未配(None)时 C-01 仍默认开启(既有契约不变)。"""
    _swap_cps_agent(monkeypatch)
    from app.projects.initial_review.infrastructure.config import (
        load_initial_review_settings,
    )

    assert load_initial_review_settings(env={}).model_check.enabled is True
