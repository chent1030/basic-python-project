"""single 拓扑运行器 —— 单 agent + 工具循环。

最简单的拓扑:直接用 config.backend 选的后端跑 ctx.messages。
支持 deepagents(自带规划/文件系统/工具)和 agentscope(ReAct 工具循环)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.backends import build_backend
from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext


class SingleRunner(BaseRunner):
    topology = "single"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._backend = build_backend(cfg)

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        return await self._backend.invoke(ctx)


__all__ = ["SingleRunner"]
