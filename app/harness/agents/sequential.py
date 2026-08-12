"""流水线,前者输出喂后者。"""
from __future__ import annotations

from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BaseSequentialAgent(BaseAgent):
    members: list[type[BaseAgent]] = []

    async def _execute_topology(self, ctx):
        current = ctx.last_user_message
        steps = []
        for m in self.members:
            result = await self._run_member(m, current, ctx)
            current = result.output
            steps.append((m.name or m.__name__, current))
        return AgentResult(output=current, extra={"topology": "sequential", "steps": steps})
