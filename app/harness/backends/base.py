"""后端适配层 —— 屏蔽 deepagents/agentscope/llm 的差异。

每个后端实现 BaseBackend(invoke/stream),把统一的 AgentRunContext 翻译成各自库的输入。
BaseAgent 根据 backend 类属性选后端。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.harness.context import AgentResult, AgentRunContext

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


class BaseBackend(ABC):
    """后端协议。"""

    @abstractmethod
    async def invoke(self, ctx: AgentRunContext) -> str:
        """执行,返回输出文本。"""
        raise NotImplementedError

    async def stream(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        """流式输出。默认不支持(子类按需实现)。"""
        result = await self.invoke(ctx)
        yield result


def build_backend(agent: BaseAgent) -> BaseBackend:
    """根据 agent.backend 选后端。"""
    backend = agent.backend
    if backend == "llm":
        from app.harness.backends.llm_backend import LlmBackend

        return LlmBackend(agent)
    if backend == "deepagents":
        from app.harness.backends.deepagents_backend import DeepAgentsBackend

        return DeepAgentsBackend(agent)
    if backend == "agentscope":
        from app.harness.backends.agentscope_backend import AgentScopeBackend

        return AgentScopeBackend(agent)
    raise ValueError(f"未知 backend: {backend}(llm | deepagents | agentscope)")


__all__ = ["BaseBackend", "build_backend"]
