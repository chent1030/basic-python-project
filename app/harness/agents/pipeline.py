"""顺序步骤中可嵌入并行步骤 + 自定义聚合。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.harness.aggregators import get_aggregator
from app.harness.base import BaseAgent
from app.harness.context import AgentResult


@dataclass
class PipelineStep:
    """pipeline 的一个步骤。run=单agent类,parallel=[agent类列表]。"""
    run: type[BaseAgent] | None = None
    parallel: list[type[BaseAgent]] = field(default_factory=list)
    aggregator: str = "merge"
    name: str = ""

class BasePipelineAgent(BaseAgent):
    steps: list[PipelineStep] = []

    async def _execute_topology(self, ctx):
        current = ctx.last_user_message
        step_outputs: dict[str, str] = {}
        trace = []
        for i, step in enumerate(self.steps):
            step_input = current
            if step.parallel:
                tasks = [self._run_member(m, step_input, ctx) for m in step.parallel]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                outputs = []
                for m, r in zip(step.parallel, results, strict=False):
                    n = m.name or m.__name__
                    outputs.append((n, f"[ERROR] {r}" if isinstance(r, Exception) else r.output))
                merged = get_aggregator(step.aggregator)(outputs)
                current = merged
                trace.append({"step": step.name or f"parallel_{i}", "kind": "parallel", "outputs": outputs})
            elif step.run:
                result = await self._run_member(step.run, step_input, ctx)
                current = result.output
                trace.append({"step": step.name or step.run.__name__, "kind": "single"})
            if step.name:
                step_outputs[step.name] = current
        return AgentResult(output=current, extra={"topology": "pipeline", "steps": trace, "step_outputs": step_outputs})
