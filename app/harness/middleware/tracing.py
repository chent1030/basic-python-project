"""Tracing 中间件 —— 运行追踪(步数/耗时/结构化日志)。"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.harness.middleware.base import MiddlewareBase

if TYPE_CHECKING:
    pass

class TracingMiddleware(MiddlewareBase):
    name = "tracing"
    async def before_invoke(self, ctx, agent):
        ctx.extra["_tracing_start"] = time.monotonic()
        ctx.extra["_tracing_msgs"] = len(ctx.messages)
        ctx.logger.info("[tracing] before agent=%s msgs=%d", ctx.agent_name, len(ctx.messages))
        return ctx
    async def after_invoke(self, ctx, agent, result):
        start = ctx.extra.get("_tracing_start")
        dur = int((time.monotonic() - start) * 1000) if start else -1
        ctx.logger.info("[tracing] after agent=%s duration=%dms output=%dchars", ctx.agent_name, dur, len(result.output))
        result.extra["tracing"] = {"duration_ms": dur, "output_chars": len(result.output)}
        return result
