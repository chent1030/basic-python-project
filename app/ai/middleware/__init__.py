"""中间件 pipeline 工厂 —— 按 agent config 选取中间件,洋葱模型执行。

build_factory() 返回一个 MiddlewarePipeline,gateway 用它:
- pipeline.before(ctx, cfg):按 config.middleware 顺序跑各中间件 before_invoke
- pipeline.after(ctx, cfg, result):逆序跑各中间件 after_invoke

中间件注册表:name -> 构造器(惰性 import,避免启动时全量 import 重依赖)。
agent config.yml 的 middleware 字段引用这些 name。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig

log = get_logger("app.ai.middleware")


# 中间件注册表:name -> (惰性)构造器。延迟 import 避免循环依赖/重依赖。
def _middleware_constructors() -> dict[str, callable]:
    """返回 name -> 返回 MiddlewareBase 实例的 0 参构造器(惰性)。"""
    def _mk(mod: str, cls: str):
        def _ctor():
            import importlib

            m = importlib.import_module(f"app.ai.middleware.{mod}")
            return getattr(m, cls)()

        return _ctor

    return {
        "tracing": _mk("tracing", "TracingMiddleware"),
        "context_memory": _mk("context_memory", "ContextMemoryMiddleware"),
        "session_memory": _mk("session_memory", "SessionMemoryMiddleware"),
        "persistent_memory": _mk("persistent_memory", "PersistentMemoryMiddleware"),
        "external_memory": _mk("external_memory", "ExternalMemoryMiddleware"),
        "filter": _mk("filter", "FilterMiddleware"),
    }


class MiddlewarePipeline:
    """洋葱模型中间件链。按 agent config 选取实例,顺序 before / 逆序 after。"""

    def __init__(self) -> None:
        self._ctors = _middleware_constructors()
        self._instances: dict[str, MiddlewareBase] = {}

    def _get(self, name: str) -> MiddlewareBase:
        if name not in self._instances:
            ctor = self._ctors.get(name)
            if ctor is None:
                raise ValueError(
                    f"未知中间件 '{name}'。可用: {list(self._ctors)}"
                )
            self._instances[name] = ctor()
        return self._instances[name]

    async def before(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        """按 config.middleware 顺序跑 before_invoke。"""
        for spec in cfg.middleware:
            try:
                mw = self._get(spec.name)
                ctx = await mw.before_invoke(ctx, cfg)
            except Exception:
                log.exception("中间件 before 失败 name=%s agent=%s", spec.name, ctx.agent_name)
        return ctx

    async def after(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        """按 config.middleware 逆序跑 after_invoke。"""
        for spec in reversed(cfg.middleware):
            try:
                mw = self._get(spec.name)
                result = await mw.after_invoke(ctx, cfg, result)
            except Exception:
                log.exception("中间件 after 失败 name=%s agent=%s", spec.name, ctx.agent_name)
        return result


def build_factory() -> MiddlewarePipeline:
    """gateway startup 时调用,返回 pipeline 单例。"""
    return MiddlewarePipeline()


__all__ = ["MiddlewareBase", "MiddlewarePipeline", "build_factory"]
