"""会话记忆中间件 —— load 历史 + append 本轮(业务库)。"""
from __future__ import annotations

from app.harness.middleware.base import MiddlewareBase


class SessionMemoryMiddleware(MiddlewareBase):
    name = "session_memory"
    async def before_invoke(self, ctx, agent):
        if not ctx.session_id:
            return ctx
        # 惰性加载历史(DB 不可用时静默降级)
        try:
            from app.harness.memory.session import session_store
            history = await session_store.load_history(ctx.agent_name, ctx.session_id)
            if history:
                system_msgs = [m for m in ctx.messages if m.get("role") == "system"]
                rest = [m for m in ctx.messages if m.get("role") != "system"]
                ctx.messages = system_msgs + history + rest
        except Exception:
            pass
        return ctx
    async def after_invoke(self, ctx, agent, result):
        if not ctx.session_id:
            return result
        try:
            from app.harness.memory.session import session_store
            await session_store.append_message(
                ctx.agent_name, ctx.session_id, "user", ctx.last_user_message
            )
            await session_store.append_message(
                ctx.agent_name, ctx.session_id, "assistant", result.output
            )
        except Exception:
            pass
        return result
