"""上下文记忆中间件 —— 透传(交后端原生管理)。"""
from __future__ import annotations

from app.harness.middleware.base import MiddlewareBase


class ContextMemoryMiddleware(MiddlewareBase):
    name = "context_memory"
    async def before_invoke(self, ctx, agent):
        ctx.extra["context_memory"] = {"source": "backend-native", "msgs": len(ctx.messages)}
        return ctx
    async def after_invoke(self, ctx, agent, result):
        result.extra.setdefault("memory_sources", []).append("context:native")
        return result
