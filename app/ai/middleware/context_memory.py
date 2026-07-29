"""context_memory 中间件 —— 上下文记忆(透传)。

上下文记忆 = 单次推理内的即时状态,依赖后端原生机制管理:
- deepagents:checkpointer + thread_id(本框架暂未配 checkpointer,可后续接)
- agentscope:agent 内置 memory(自动维护对话内消息)

本中间件是「占位 + 标注」,不做实际存储 —— 只在 ctx.extra 标注 context 来源,
并把单次推理的消息原样透传给后端。它的存在让 agent config 能显式声明「我用了上下文记忆」,
方便可观测性追踪 + 后续替换为自定义实现。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig


class ContextMemoryMiddleware(MiddlewareBase):
    name = "context_memory"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        ctx.extra["context_memory"] = {"source": "backend-native", "msgs": len(ctx.messages)}
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        result.extra.setdefault("memory_sources", []).append("context:native")
        return result


__all__ = ["ContextMemoryMiddleware"]
