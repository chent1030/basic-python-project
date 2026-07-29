"""中间件基类 + 洋葱模型执行器。

四类记忆 + tracing + filter 都实现 MiddlewareBase:
- before_invoke:运行后端前,可修改 ctx.messages(注入历史/记忆/知识、过滤输入)
- after_invoke: 运行后端后,可修改 result(过滤输出、写记忆、记录步数)

洋葱模型:before 按声明顺序执行,after 按逆序执行(像洋葱层层包裹)。
中间件未启用时退化为 no-op(不报错),保证 agent config 挂了但全局没配也能跑。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig


class MiddlewareBase:
    """所有中间件的基类。子类按需重写 before/after。"""

    name: str = "base"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        """运行后端前。默认不改;子类可修改 ctx.messages 后返回 ctx。"""
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        """运行后端后。默认不改;子类可修改 result 后返回。"""
        return result


__all__ = ["MiddlewareBase"]
