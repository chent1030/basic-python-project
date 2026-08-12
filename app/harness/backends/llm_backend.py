"""LLM 后端 —— 最简,直接调 llm.invoke,无 agent 循环。

适合不需要工具调用/多步推理的简单判断(规则类、轻量 AI 检查)。
最快最省 token。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.harness.backends.base import BaseBackend
from app.harness.context import AgentRunContext

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


class LlmBackend(BaseBackend):
    """直接调 llm.invoke。"""

    def __init__(self, agent: BaseAgent) -> None:
        self.agent = agent

    async def invoke(self, ctx: AgentRunContext) -> str:
        from app.services.llm import llm

        return await llm.invoke(
            ctx.messages,
            provider=self.agent.provider or None,
            model=self.agent.model,
            temperature=self.agent.temperature,
        )

    async def stream(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        from app.services.llm import llm

        async for chunk in llm.invoke_stream(
            ctx.messages,
            provider=self.agent.provider or None,
            model=self.agent.model,
            temperature=self.agent.temperature,
        ):
            yield chunk


__all__ = ["LlmBackend"]
