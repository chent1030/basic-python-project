"""多 agent 并行跑同输入,汇总。"""
from __future__ import annotations

import asyncio

from app.harness.aggregators import get_aggregator
from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BaseParallelAgent(BaseAgent):
    members: list[type[BaseAgent]] = []
    aggregator: str = "merge"

    async def _execute_topology(self, ctx):
        message = ctx.last_user_message
        tasks = [self._run_member(m, message, ctx) for m in self.members]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        outputs = []
        for m, r in zip(self.members, results, strict=False):
            name = m.name or m.__name__
            if isinstance(r, Exception):
                outputs.append((name, f"[ERROR] {r}"))
            else:
                outputs.append((name, r.output))
        agg = get_aggregator(self.aggregator)
        merged = agg(outputs)
        return AgentResult(output=merged, extra={"topology": "parallel", "members": outputs})
