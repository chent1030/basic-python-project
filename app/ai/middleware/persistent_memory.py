"""persistent_memory 中间件 —— 持久记忆(独立向量库 pgvector)。

before: 用最后一条 user 消息向量召回相关记忆,拼成 system 提示注入 ctx.messages
after:  从本轮对话提取新事实/偏好写入向量库(跨会话长期记忆)

与 session 区别:persistent 跨会话(session 是单会话内),且走向量召回(语义匹配)。
persistent_memory.enabled=false 时,MemoryStore 退化为 no-op,本中间件也基本不做事。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.memory import memory_store
from app.ai.middleware.base import MiddlewareBase

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig


class PersistentMemoryMiddleware(MiddlewareBase):
    name = "persistent_memory"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        query = ctx.last_user_message
        if not query:
            return ctx
        memories = await memory_store.recall(
            ctx.agent_name, query, user_id=ctx.user_id
        )
        if not memories:
            return ctx
        block = "\n".join(f"- {m}" for m in memories)
        # 把记忆作为额外 system 消息插入(在原 system 之后、对话之前)
        ctx.messages.insert(
            1 if ctx.messages and ctx.messages[0].get("role") == "system" else 0,
            {"role": "system", "content": f"[相关记忆]\n{block}"},
        )
        ctx.logger.info(
            "[persistent_memory] 召回 agent=%s 条数=%d", ctx.agent_name, len(memories)
        )
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        # 简单策略:把 user 消息 + assistant 回复作为一条事实存入(供下次召回)。
        # 生产环境可换成 LLM 提取关键事实。失败不影响主流程。
        fact = f"Q: {ctx.last_user_message}\nA: {result.output[:500]}"
        await memory_store.remember(
            ctx.agent_name, fact, user_id=ctx.user_id
        )
        result.extra.setdefault("memory_sources", []).append("persistent:saved")
        return result


__all__ = ["PersistentMemoryMiddleware"]
