"""推送适配（C6 — §21.5 / 设计 §3.3 / AC-29）。

本期仅预留接口（channel = interface only），不接入任何外部渠道；
默认实现 :class:`NoOpPusher` 写入 push_status=UNCONFIGURED + 原因
「推送未配置」（对齐 AC-29：未接入必须显示「推送未配置」不得记为发送成功）。

后续 wave 接入邮件 / 钉钉 / Webhook 等渠道时，新增类实现
:class:`WeeklyReportPusher` Protocol，在 service 装配时注入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.projects.weekly_report.domain.models import PushResult


@runtime_checkable
class WeeklyReportPusher(Protocol):
    """周报推送适配抽象接口。

    实现语义：
    - ``ok=True`` + ``status="SUCCESS"`` → 已成功投递；
    - ``ok=False`` + ``status="FAILED"`` → 技术失败（HTTP / 鉴权 / 限额）；
    - ``status="SKIPPED"``             → 渠道已配置但本期跳过（如节假日）；
    - ``status="UNCONFIGURED"``        → 渠道未注册（默认 NoOpPusher 语义）。

    抛异常视为 FAILED（service 层捕获后转 PushResult.ok=False）。
    """

    async def send(
        self,
        *,
        report_run_id: str,
        payload: dict[str, Any],
    ) -> PushResult:
        ...


@dataclass(frozen=True)
class NoOpPusher:
    """未注册渠道时使用的占位推送器。

    永远返回 ``UNCONFIGURED`` + reason「推送未配置」（AC-29）：
    - 不伪造「已发送」；
    - 不抛异常（业务流程不应被「未配置」阻塞）；
    - 落库 push_result 含中文原因（运维查询友好）。
    """

    reason: str = "推送未配置"

    async def send(
        self,
        *,
        report_run_id: str,
        payload: dict[str, Any],
    ) -> PushResult:
        return PushResult(
            ok=False,
            status="UNCONFIGURED",
            detail=f"run_id={report_run_id} reason={self.reason}",
            attempts=0,
        )


def default_pusher() -> WeeklyReportPusher:
    """service 装配入口；后续可改为渠道 registry 解析。"""
    return NoOpPusher()


__all__ = [
    "NoOpPusher",
    "WeeklyReportPusher",
    "default_pusher",
]