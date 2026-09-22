"""初审执行配置（env 优先于 yaml，代码层读取）。

- yaml 来源：config/config.yaml 的 ``cps_agent`` 节（app/core/config.py Settings）。
- env 覆盖：``CPS_JAVA_CALLBACK_BASE_URL`` 优先于 yaml
  ``cps_agent.java_callback.base_url``（回调地址随部署环境变化，属 12-factor 口径）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.core.config import settings

#: Java 回调基址的 env 覆盖名（待联调项：与 Java 侧部署清单同步登记）。
ENV_JAVA_CALLBACK_BASE_URL = "CPS_JAVA_CALLBACK_BASE_URL"


@dataclass(frozen=True)
class InitialReviewSettings:
    """运行时冻结配置快照（服务构造时读取一次）。"""

    deadline_seconds: float  # 整任务 wall-clock 上限：480s = 8 分钟（设计 §3.1:154）
    java_callback_base: str
    java_callback_path: str
    callback_timeout_seconds: float
    callback_max_retries: int  # 技术重试 ≤2（30s/60s 退避，设计 §3.1:157）
    callback_backoff_seconds: tuple[float, ...]


def load_initial_review_settings(env: dict[str, str] | None = None) -> InitialReviewSettings:
    env = os.environ if env is None else env
    cfg = settings.cps_agent
    base = env.get(ENV_JAVA_CALLBACK_BASE_URL) or cfg.java_callback.base_url
    backoff = tuple(cfg.java_callback.retry_backoff_seconds)
    return InitialReviewSettings(
        deadline_seconds=cfg.initial_review.deadline_seconds,
        java_callback_base=base.rstrip("/"),
        java_callback_path=cfg.java_callback.path,
        callback_timeout_seconds=cfg.java_callback.timeout_seconds,
        callback_max_retries=cfg.java_callback.max_retries,
        callback_backoff_seconds=backoff,
    )
