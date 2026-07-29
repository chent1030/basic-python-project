"""sequential 拓扑运行器 —— 流水线,前者输出喂后者(agentscope SequentialPipeline 思路)。

成员按顺序执行,每个成员的输出作为下一个成员的输入。
最终输出 = 最后一个成员的输出。

每个成员通过 run_member 回调跑(走完整 run 流程 → 树状记录)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.sequential")


class SequentialRunner(BaseRunner):
    topology = "sequential"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        message = ctx.last_user_message
        steps: list[tuple[str, str]] = []
        current = message
        for name in self.cfg.members:
            result = await run_member(name, current, ctx)
            current = result.output
            steps.append((name, current))
        return AgentResult(
            output=current,
            extra={"topology": "sequential", "steps": steps},
        )


__all__ = ["SequentialRunner"]
