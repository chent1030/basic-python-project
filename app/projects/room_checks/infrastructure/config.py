"""C-04 辅房点检视觉判定独立开关解析(波次 7,清单⑥)。

历史:波次 4 起 C-04 与 C-01 初审共用 ``cps_agent.initial_review.
model_check.enabled``(同开同关);波次 7 拆为独立开关
``cps_agent.room_checks.vision.enabled``。

回退链(新开关全域优先于旧开关,同级内 env 优先于 yaml;全链未配 → 关):

1. env ``CPS_ROOM_CHECKS_VISION_ENABLED``   —— C-04 专属 env(新)
2. yaml ``cps_agent.room_checks.vision.enabled`` —— 本节点(新;None=未配)
3. env ``CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED`` —— 旧共享 env(兼容)
4. yaml ``cps_agent.initial_review.model_check.enabled`` —— 旧共享 yaml(兼容)
5. 以上全未配 → ``False``(SKIPPED 不伪造)

「老配置仍生效」:只配了旧开关(3/4)的既有部署,C-04 行为与波次 4-6 完全
一致;一旦显式配置新开关(1/2),C-04 即与 C-01 解耦,互不影响。
"""

from __future__ import annotations

import os

from app.core.config import settings

#: C-04 视觉判定专属 env(新;部署层覆盖 yaml)
ENV_ROOM_CHECKS_VISION_ENABLED = "CPS_ROOM_CHECKS_VISION_ENABLED"

#: 旧共享 env(initial_review 的模型检查开关;波次 4-6 C-04 受其控制)
ENV_LEGACY_MODEL_CHECK_ENABLED = "CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED"

_TRUTHY = ("1", "true", "yes", "on")


def _parse_env_bool(raw: str) -> bool:
    return raw.strip().lower() in _TRUTHY


def resolve_vision_enabled(
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """解析 C-04 视觉判定开关;返回 ``(enabled, 决策来源说明)``。

    说明字符串用于 SKIPPED 日志/理由,让运维能看出是哪个开关关掉了判定
    (排查「为什么 SKIPPED」时不需要翻代码)。
    """
    env = os.environ if env is None else env

    # 1) 新 env
    raw = env.get(ENV_ROOM_CHECKS_VISION_ENABLED)
    if raw is not None:
        return _parse_env_bool(raw), f"env {ENV_ROOM_CHECKS_VISION_ENABLED}={raw}"

    # 2) 新 yaml 节点
    vision = settings.cps_agent.room_checks.vision.enabled
    if vision is not None:
        return vision, f"cps_agent.room_checks.vision.enabled={vision}"

    # 3) 旧共享 env(波次 4-6 兼容)
    raw = env.get(ENV_LEGACY_MODEL_CHECK_ENABLED)
    if raw is not None:
        return (
            _parse_env_bool(raw),
            f"回退旧开关 env {ENV_LEGACY_MODEL_CHECK_ENABLED}={raw}",
        )

    # 4) 旧共享 yaml(波次 4-6 兼容)
    legacy = settings.cps_agent.initial_review.model_check.enabled
    if legacy is not None:
        return (
            legacy,
            f"回退旧开关 cps_agent.initial_review.model_check.enabled={legacy}",
        )

    # 5) 全链未配 → 关(不伪造判定)
    return False, "全部开关未配置(默认 false)"


__all__ = [
    "ENV_LEGACY_MODEL_CHECK_ENABLED",
    "ENV_ROOM_CHECKS_VISION_ENABLED",
    "resolve_vision_enabled",
]
