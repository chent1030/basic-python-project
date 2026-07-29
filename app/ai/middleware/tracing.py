"""tracing 中间件 —— 运行追踪(步数/耗时),复用结构化日志。

before: 记录开始时间 + 初始消息数到 ctx.extra
after:  算耗时、记录步数(消息数变化)、写结构化日志

这是「运行状态监控」的实时侧(与 agent_runs 表的持久侧互补):
- agent_runs:持久、可查询、可做看板(RunStore 写)
- tracing 日志:实时、带 request_id、可在日志系统里追踪
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig

_TRACING_START = "_tracing_start_ts"
_TRACING_MSGS = "_tracing_initial_msgs"


class TracingMiddleware(MiddlewareBase):
    name = "tracing"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        ctx.extra[_TRACING_START] = time.monotonic()
        ctx.extra[_TRACING_MSGS] = len(ctx.messages)
        ctx.logger.info(
            "[tracing] before agent=%s depth=%d msgs=%d source=%s",
            ctx.agent_name, ctx.depth, len(ctx.messages), ctx.source,
        )
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        start = ctx.extra.get(_TRACING_START)
        dur = int((time.monotonic() - start) * 1000) if start else -1
        initial = ctx.extra.get(_TRACING_MSGS, len(ctx.messages))
        ctx.logger.info(
            "[tracing] after agent=%s duration=%dms output_chars=%d tokens=%s",
            ctx.agent_name, dur, len(result.output), result.tokens,
        )
        # 把追踪信息挂到 result.extra(供 API/调试看)
        result.extra["tracing"] = {
            "duration_ms": dur,
            "messages_in": initial,
            "output_chars": len(result.output),
        }
        return result


__all__ = ["TracingMiddleware"]
