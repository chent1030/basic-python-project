"""中间件基类 + Pipeline(洋葱模型)。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.context import AgentResult, AgentRunContext

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


class MiddlewareBase:
    """中间件基类。before_invoke 在 agent 执行前,after_invoke 在后。"""

    name: str = ""

    async def before_invoke(self, ctx: AgentRunContext, agent: BaseAgent) -> AgentRunContext:
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, agent: BaseAgent, result: AgentResult
    ) -> AgentResult:
        return result


class MiddlewarePipeline:
    """中间件 pipeline(单例缓存)。按 agent.middleware 声明顺序执行。"""

    def __init__(self) -> None:
        self._instances: dict[str, MiddlewareBase] = {}

    def get(self, name: str) -> MiddlewareBase | None:
        if name not in self._instances:
            ctor = _CONSTRUCTORS.get(name)
            if ctor is None:
                return None
            self._instances[name] = ctor()
        return self._instances[name]


def _mk(mod: str, cls: str):
    def _ctor():
        import importlib

        m = importlib.import_module(f"app.harness.middleware.{mod}")
        return getattr(m, cls)()
    return _ctor


_CONSTRUCTORS: dict[str, Any] = {
    "tracing": _mk("tracing", "TracingMiddleware"),
    "context_memory": _mk("context_memory", "ContextMemoryMiddleware"),
    "session_memory": _mk("session_memory", "SessionMemoryMiddleware"),
    "summarization": _mk("summarization", "SummarizationMiddleware"),
    "filter": _mk("filter", "FilterMiddleware"),
}

_pipeline: MiddlewarePipeline | None = None


def get_pipeline() -> MiddlewarePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = MiddlewarePipeline()
    return _pipeline


__all__ = ["MiddlewareBase", "MiddlewarePipeline", "get_pipeline"]
