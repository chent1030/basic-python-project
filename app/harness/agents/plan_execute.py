"""planner 规划步骤 → executor 逐步执行。"""
from __future__ import annotations

import json
import re

from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BasePlanExecuteAgent(BaseAgent):
    planner: type[BaseAgent] = None
    executor: type[BaseAgent] = None
    max_steps: int = 10

    async def _execute_topology(self, ctx):
        plan_prompt = f'把任务拆解成步骤。只输出 JSON: {{"steps": ["步骤1","步骤2"]}}\n任务: {ctx.last_user_message}'
        plan_result = await self._run_member(self.planner, plan_prompt, ctx)
        steps = self._parse_steps(plan_result.output)
        exec_cls = self.executor or self.planner
        current = ctx.last_user_message
        for i, step in enumerate(steps, 1):
            result = await self._run_member(exec_cls, f"执行步骤 {i}/{len(steps)}: {step}\n上下文: {current}", ctx)
            current = result.output
        return AgentResult(output=current, extra={"topology": "plan_execute", "plan": steps})

    @staticmethod
    def _parse_steps(text, max_steps=10):
        m = re.search(r'\{[^{}]*"steps"[^{}]*\}', text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                steps = data.get("steps", [])
                if isinstance(steps, list) and steps:
                    return [str(s) for s in steps[:max_steps]]
            except json.JSONDecodeError:
                pass
        lines = []
        for line in text.strip().splitlines():
            line = re.sub(r'^\s*(\d+[\.\)、]|[-*•])\s*', '', line).strip()
            if line:
                lines.append(line)
        return lines[:max_steps]
