"""摘要中间件 —— 历史过长时自动 LLM 摘要压缩。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.middleware.base import MiddlewareBase

if TYPE_CHECKING:
    pass

class SummarizationMiddleware(MiddlewareBase):
    name = "summarization"
    async def before_invoke(self, ctx, agent):
        if len(ctx.messages) <= 20:
            return ctx
        non_system = [m for m in ctx.messages if m.get("role") != "system"]
        to_summarize = non_system[:len(non_system) - 4]
        recent = non_system[len(non_system) - 4:]
        if not to_summarize:
            return ctx
        try:
            from langchain_core.messages import HumanMessage

            from app.services.llm import llm
            history = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
            summary = await llm.invoke([HumanMessage(content=f"摘要以下对话:\n{history}")], provider=agent.provider or None)
            system_msgs = [m for m in ctx.messages if m.get("role") == "system"]
            ctx.messages = system_msgs + [{"role": "system", "content": f"[历史摘要]\n{summary}"}] + recent
        except Exception:
            pass
        return ctx
    async def after_invoke(self, ctx, agent, result):
        return result
