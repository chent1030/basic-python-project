"""session_memory 中间件 —— 会话记忆(业务库 agent_sessions 表)。

before: load 该 session 的历史消息,prepend 到 ctx.messages(在 system 之后)
after:  把本轮 user + assistant 回复存入 agent_sessions

这是「持续对话」的核心:跨多次调用记住同一 session_id 的往复历史。
只对有 session_id 的运行生效(trigger 模式 session_id=None 时 no-op)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase
from app.ai.session import session_store

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig


class SessionMemoryMiddleware(MiddlewareBase):
    name = "session_memory"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        if not ctx.session_id:
            return ctx  # 无 session(trigger 模式),不加载历史
        history = await session_store.load_history(ctx.agent_name, ctx.session_id)
        if not history:
            return ctx
        # 把历史插到 system 之后、本轮消息之前
        system_msgs = [m for m in ctx.messages if m.get("role") == "system"]
        rest = [m for m in ctx.messages if m.get("role") != "system"]
        ctx.messages = system_msgs + history + rest
        ctx.logger.info(
            "[session_memory] 加载历史 agent=%s session=%s 条数=%d",
            ctx.agent_name, ctx.session_id, len(history),
        )
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        if not ctx.session_id:
            return result  # 无 session(trigger 模式),不存历史,原样返回 result
        # 存本轮 user + assistant(失败只记日志,不影响主流程)
        user_msg = ctx.last_user_message
        await session_store.append_turn(
            ctx.agent_name, ctx.session_id, user_msg, result.output
        )
        result.extra.setdefault("memory_sources", []).append("session:saved")
        return result


__all__ = ["SessionMemoryMiddleware"]
