"""domain 模型：纯 dataclass（无 ORM 依赖）。

- ``ReportWindow``：窗口口径 [start, end)，含头不含尾（AC-28 / 设计 §3.3）；
- ``ReportKey``：run_id / period / report_type 三元组（业务键）；
- ``PushResult``：推送结果（解耦 status / push_status）；
- ``WeeklyReportStatus`` / ``WeeklyReportPushStatus``：枚举字符串常量。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ---------------- 状态常量（字符串，对齐 ORM 字段语义约束） ----------------
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_ARCHIVING = "ARCHIVING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

PUSH_STATUS_PENDING = "PENDING"
PUSH_STATUS_SUCCESS = "SUCCESS"
PUSH_STATUS_FAILED = "FAILED"
PUSH_STATUS_SKIPPED = "SKIPPED"
PUSH_STATUS_UNCONFIGURED = "UNCONFIGURED"

# 设计 §3.3 / AC-28：周报窗口左闭右开，含头不含尾
WINDOW_INCLUSIVE_START = True
WINDOW_EXCLUSIVE_END = True


@dataclass(frozen=True)
class ReportWindow:
    """周报窗口（UTC，tz-aware）。

    ``window_start`` 含（业务时间 >= window_start），
    ``window_end`` 不含（业务时间 < window_end）。
    """

    window_start: datetime
    window_end: datetime

    def contains(self, dt: datetime) -> bool:
        return self.window_start <= dt < self.window_end

    def period_iso(self) -> str:
        """``yyyy-Ww`` ISO 周编号（基于 window_start，UTC 周一为周首）。"""
        iso = self.window_start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"


@dataclass(frozen=True)
class ReportKey:
    """业务主键三元组：report_type + window（数据库 UNIQUE 约束对应）。"""

    report_type: str
    window: ReportWindow


@dataclass(frozen=True)
class PushResult:
    """推送结果（解耦 status / push_status，独立列存）。

    - ``ok``            ：推送调用是否成功；
    - ``status``        ：落库 push_status 取值；
    - ``detail``        ：保存到 ``push_result`` 列（错误信息 / 渠道响应 / 跳过原因）；
    - ``attempts``      ：实际尝试次数（重试链路后续 wave 接）；
    """

    ok: bool
    status: str  # SUCCESS / FAILED / SKIPPED / UNCONFIGURED
    detail: str = ""
    attempts: int = 1


__all__ = [
    "PUSH_STATUS_FAILED",
    "PUSH_STATUS_PENDING",
    "PUSH_STATUS_SKIPPED",
    "PUSH_STATUS_SUCCESS",
    "PUSH_STATUS_UNCONFIGURED",
    "PushResult",
    "ReportKey",
    "ReportWindow",
    "STATUS_ARCHIVING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "WINDOW_EXCLUSIVE_END",
    "WINDOW_INCLUSIVE_START",
]