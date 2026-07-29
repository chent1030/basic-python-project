"""external_memory 中间件 —— 外部记忆(外部 API 召回)。

before: 把最后一条 user 消息 POST 到配置的外部知识接口,取回的知识片段注入 ctx.messages
after:  无(外部记忆只读召回,不回写)

第一期只覆盖「外部 API」一种外部记忆形态。
pgvector 知识库 / 业务 DB 查询等其它外部记忆形式留接口(后续可加 rag_kb / rag_db 中间件)。
external_memory.enabled=false 时 no-op。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase
from app.core.config import settings

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig


class ExternalMemoryMiddleware(MiddlewareBase):
    name = "external_memory"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        em = settings.agents.external_memory
        if not (em.enabled and em.url):
            return ctx
        query = ctx.last_user_message
        if not query:
            return ctx
        try:
            from app.services.http_client import http_client

            resp = await http_client.request(
                em.method,
                em.url,
                headers=em.headers,
                json={"query": query, "agent": ctx.agent_name},
                timeout=em.timeout,
            )
            data = resp.json()
            # 期望接口返回 {"chunks": ["...", ...]} 或 {"results": [...]} 或纯字符串列表
            chunks: list[str] = []
            if isinstance(data, dict):
                chunks = data.get("chunks") or data.get("results") or []
            elif isinstance(data, list):
                chunks = data
            chunks = [str(c) for c in chunks if c]
            if not chunks:
                return ctx
            block = "\n".join(f"- {c}" for c in chunks)
            ctx.messages.insert(
                1 if ctx.messages and ctx.messages[0].get("role") == "system" else 0,
                {"role": "system", "content": f"[外部知识]\n{block}"},
            )
            ctx.logger.info(
                "[external_memory] 召回 agent=%s 条数=%d", ctx.agent_name, len(chunks)
            )
        except Exception:
            ctx.logger.exception("[external_memory] 召回失败 agent=%s", ctx.agent_name)
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        result.extra.setdefault("memory_sources", []).append("external:read-only")
        return result


__all__ = ["ExternalMemoryMiddleware"]
