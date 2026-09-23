"""初审执行配置（env 优先于 yaml，代码层读取）。

- yaml 来源：config/config.yaml 的 ``cps_agent`` 节（app/core/config.py Settings）；
- env 覆盖：``CPS_JAVA_CALLBACK_BASE_URL``（回调地址）、
  ``CPS_STORAGE_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET``（RustFS，变量名与
  Java 侧 application.yml 的 cps.storage.* 占位符一致，部署同一套 env 即可）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.config import settings

#: env 覆盖名（待联调项：与 Java 侧部署清单同步登记）。
ENV_JAVA_CALLBACK_BASE_URL = "CPS_JAVA_CALLBACK_BASE_URL"
ENV_STORAGE_ENDPOINT = "CPS_STORAGE_ENDPOINT"
ENV_STORAGE_ACCESS_KEY = "CPS_STORAGE_ACCESS_KEY"
ENV_STORAGE_SECRET_KEY = "CPS_STORAGE_SECRET_KEY"
ENV_STORAGE_BUCKET = "CPS_STORAGE_BUCKET"
#: 装配冒烟/降级开关：false → A7/A8 SKIPPED（不触 LLM；A6 确定性检查照常）
ENV_MODEL_CHECK_ENABLED = "CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED"


@dataclass(frozen=True)
class RustFSSettings:
    """object_key 附件读取（S3 SigV4 GET，零新增依赖：httpx + stdlib）。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @property
    def unavailable_reason(self) -> str | None:
        """配置不完整的原因（完整 → None）。"""
        missing = [
            name
            for name, value in (
                ("endpoint", self.endpoint),
                ("access_key", self.access_key),
                ("secret_key", self.secret_key),
                ("bucket", self.bucket),
            )
            if not value
        ]
        if missing:
            return "RustFS 配置不完整（缺少 " + "/".join(missing) + "）"
        return None


@dataclass(frozen=True)
class ModelCheckSettings:
    """A7/A8 模型检查（qwen 系，OpenAI 兼容模式）。"""

    enabled: bool
    provider: str
    text_model: str
    vision_model: str
    temperature: float
    field_budget_seconds: float
    max_images_per_side: int


@dataclass(frozen=True)
class SimilaritySettings:
    """A6 雷同性阈值（D-05：待业务样本校准）。"""

    same_fail: float
    same_warn: float
    history_fail: float
    history_warn: float


@dataclass(frozen=True)
class InitialReviewSettings:
    """运行时冻结配置快照（服务构造时读取一次）。"""

    deadline_seconds: float  # 整任务 wall-clock 上限：480s = 8 分钟（设计 §3.1:154）
    java_callback_base: str
    java_callback_path: str
    callback_timeout_seconds: float
    callback_max_retries: int  # 技术重试 ≤2（30s/60s 退避，设计 §3.1:157）
    callback_backoff_seconds: tuple[float, ...]
    similarity: SimilaritySettings = field(
        default_factory=lambda: SimilaritySettings(0.90, 0.75, 0.85, 0.70)
    )
    model_check: ModelCheckSettings = field(
        default_factory=lambda: ModelCheckSettings(
            True, "qwen", "qwen-plus", "Qwen2.5-VL-7B-Instruct", 0.1, 90.0, 4
        )
    )
    rustfs: RustFSSettings = field(
        default_factory=lambda: RustFSSettings("", "", "", "")
    )


def load_initial_review_settings(env: dict[str, str] | None = None) -> InitialReviewSettings:
    env = os.environ if env is None else env
    cfg = settings.cps_agent
    base = env.get(ENV_JAVA_CALLBACK_BASE_URL) or cfg.java_callback.base_url
    backoff = tuple(cfg.java_callback.retry_backoff_seconds)
    ir = cfg.initial_review
    mc = ir.model_check
    rs = ir.rustfs
    # 波次 7:model_check.enabled 支持 None=未配置;initial_review 自身未配时
    # 维持既有默认 True 契约(C-04 的独立回退链不经过此处,见 room_checks/config.py)。
    yaml_enabled = mc.enabled if mc.enabled is not None else True
    enabled_raw = env.get(ENV_MODEL_CHECK_ENABLED)
    enabled = (
        yaml_enabled if enabled_raw is None
        else enabled_raw.strip().lower() in ("1", "true", "yes", "on")
    )
    return InitialReviewSettings(
        deadline_seconds=ir.deadline_seconds,
        java_callback_base=base.rstrip("/"),
        java_callback_path=cfg.java_callback.path,
        callback_timeout_seconds=cfg.java_callback.timeout_seconds,
        callback_max_retries=cfg.java_callback.max_retries,
        callback_backoff_seconds=backoff,
        similarity=SimilaritySettings(
            same_fail=ir.similarity.same_fail,
            same_warn=ir.similarity.same_warn,
            history_fail=ir.similarity.history_fail,
            history_warn=ir.similarity.history_warn,
        ),
        model_check=ModelCheckSettings(
            enabled=enabled,
            provider=mc.provider,
            text_model=mc.text_model,
            vision_model=mc.vision_model,
            temperature=mc.temperature,
            field_budget_seconds=mc.field_budget_seconds,
            max_images_per_side=mc.max_images_per_side,
        ),
        rustfs=RustFSSettings(
            endpoint=env.get(ENV_STORAGE_ENDPOINT) or rs.endpoint,
            access_key=env.get(ENV_STORAGE_ACCESS_KEY) or rs.access_key,
            secret_key=env.get(ENV_STORAGE_SECRET_KEY) or rs.secret_key,
            bucket=env.get(ENV_STORAGE_BUCKET) or rs.bucket,
        ),
    )


__all__ = [
    "ENV_JAVA_CALLBACK_BASE_URL",
    "ENV_MODEL_CHECK_ENABLED",
    "ENV_STORAGE_ACCESS_KEY",
    "ENV_STORAGE_BUCKET",
    "ENV_STORAGE_ENDPOINT",
    "ENV_STORAGE_SECRET_KEY",
    "InitialReviewSettings",
    "ModelCheckSettings",
    "RustFSSettings",
    "SimilaritySettings",
    "load_initial_review_settings",
]
