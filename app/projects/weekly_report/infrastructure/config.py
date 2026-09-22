"""周报运行时配置（yaml + env 优先，复用既有 RustFS 配置 + C3 调度开关）。

- yaml: ``config/config.yaml`` 的 ``cps_agent.weekly_report`` 节（本期新增）；
- env:
    - ``CPS_STORAGE_*``         ：RustFS 接入（沿用 A7 / initial_review 同套）；
    - ``CPS_WEEKLY_REPORT_SCHED_ENABLED`` ：C3 调度开关（默认 ``false`` 防
      开发机周一早误触发；生产部署置 ``true``）；
    - ``CPS_WEEKLY_REPORT_TYPES`` ：逗号分隔配置周报分类列表（默认两分类）；
    - ``CPS_WEEKLY_REPORT_BUCKET`` ：周报独立桶；空 → 复用 attachments 桶。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.core.config import settings
from app.projects.initial_review.infrastructure.config import (  # noqa: F401  # 共用 env key
    ENV_STORAGE_ACCESS_KEY,
    ENV_STORAGE_BUCKET,
    ENV_STORAGE_ENDPOINT,
    ENV_STORAGE_SECRET_KEY,
)

# 周报专用 env
ENV_SCHED_ENABLED = "CPS_WEEKLY_REPORT_SCHED_ENABLED"
ENV_REPORT_TYPES = "CPS_WEEKLY_REPORT_TYPES"
ENV_RUSTFS_BUCKET = "CPS_WEEKLY_REPORT_BUCKET"

DEFAULT_REPORT_TYPES = ("cps-issue-inspection", "room-check")


@dataclass(frozen=True)
class WeeklyReportRustFSSettings:
    """RustFS 周报上传配置（沿用 A7 同套 SigV4；bucket 可独立）。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @property
    def unavailable_reason(self) -> str | None:
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
            return "RustFS 周报配置不完整（缺少 " + "/".join(missing) + "）"
        return None


@dataclass(frozen=True)
class WeeklyReportSettings:
    """运行时冻结配置快照。"""

    sched_enabled: bool
    report_types: tuple[str, ...]
    rustfs: WeeklyReportRustFSSettings
    push_unavailable_reason: str = "推送未配置"
    # 调度 cron 与 misfire_grace_time 由 tasks/weekly_report_task.py 显式传入
    cron_expression: str = "0 8 * * MON"
    timezone: str = "Asia/Shanghai"
    misfire_grace_time_seconds: int = 7200  # 2h 兜底


def load_weekly_report_settings(
    env: dict[str, str] | None = None,
) -> WeeklyReportSettings:
    """合并 yaml + env：env 优先；env 没设 → 用 yaml 默认。"""
    env_map = os.environ if env is None else env

    cfg = settings.cps_agent
    # 沿用 initial_review.rustfs 作为兜底（endpoint/ak/sk 与 Java 侧部署同源）
    rs = cfg.initial_review.rustfs

    # 周报专属 bucket：env → yaml.weekly_report.rustfs_bucket → attachments 桶
    weekly_cfg = getattr(cfg, "weekly_report", None)
    yaml_bucket = getattr(weekly_cfg, "rustfs_bucket", "") if weekly_cfg else ""
    rustfs_bucket = (
        env_map.get(ENV_RUSTFS_BUCKET) or yaml_bucket or rs.bucket or ""
    )

    sched_raw = env_map.get(ENV_SCHED_ENABLED)
    sched_enabled = (
        sched_raw.strip().lower() in ("1", "true", "yes", "on")
        if sched_raw is not None
        else False
    )

    types_raw = env_map.get(ENV_REPORT_TYPES)
    if types_raw:
        report_types = tuple(t.strip() for t in types_raw.split(",") if t.strip())
    else:
        report_types = DEFAULT_REPORT_TYPES

    return WeeklyReportSettings(
        sched_enabled=sched_enabled,
        report_types=report_types,
        rustfs=WeeklyReportRustFSSettings(
            endpoint=env_map.get(ENV_STORAGE_ENDPOINT) or rs.endpoint,
            access_key=env_map.get(ENV_STORAGE_ACCESS_KEY) or rs.access_key,
            secret_key=env_map.get(ENV_STORAGE_SECRET_KEY) or rs.secret_key,
            bucket=rustfs_bucket,
        ),
    )


__all__ = [
    "DEFAULT_REPORT_TYPES",
    "ENV_RUSTFS_BUCKET",
    "ENV_REPORT_TYPES",
    "ENV_SCHED_ENABLED",
    "WeeklyReportRustFSSettings",
    "WeeklyReportSettings",
    "load_weekly_report_settings",
]