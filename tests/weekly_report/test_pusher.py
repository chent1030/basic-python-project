"""C6 — 推送适配测试（§21.5 / AC-29）。

AC-29：未接入必须显示「推送未配置」不得记为发送成功。
"""
from __future__ import annotations

import pytest

from app.projects.weekly_report.domain.models import (
    PUSH_STATUS_UNCONFIGURED,
    PushResult,
)
from app.projects.weekly_report.infrastructure.pusher import NoOpPusher


@pytest.mark.asyncio
async def test_noop_pusher_returns_unconfigured():
    pusher = NoOpPusher()
    result = await pusher.send(
        report_run_id="r1", payload={"report_type": "room-check"}
    )
    assert isinstance(result, PushResult)
    assert result.status == PUSH_STATUS_UNCONFIGURED
    assert result.ok is False
    assert "推送未配置" in result.detail
    assert "r1" in result.detail


@pytest.mark.asyncio
async def test_noop_pusher_does_not_raise():
    """NoOp 必须永远返回结果，不抛异常（不应让未配置的业务流阻塞）。"""
    pusher = NoOpPusher(reason="业务渠道暂未注册")
    result = await pusher.send(
        report_run_id="abc", payload={"report_type": "room-check"}
    )
    assert result.status == PUSH_STATUS_UNCONFIGURED
    assert "业务渠道暂未注册" in result.detail


def test_noop_pusher_is_protocol():
    from app.projects.weekly_report.infrastructure.pusher import (
        WeeklyReportPusher,
    )

    assert isinstance(NoOpPusher(), WeeklyReportPusher)