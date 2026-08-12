"""executor 执行 → evaluator 评估 → 不过重试。"""
from __future__ import annotations

import json
import re

from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BaseReflectionAgent(BaseAgent):
    executor: type[BaseAgent] = None
    evaluator: type[BaseAgent] = None
    max_iterations: int = 3
    pass_threshold: float = 0.8

    async def _execute_topology(self, ctx):
        task = ctx.last_user_message
        iterations = []
        current_output = ""
        passed = False
        for i in range(1, self.max_iterations + 1):
            prompt = task
            if iterations and iterations[-1].get("feedback"):
                prompt = f"{task}\n\n上次未通过评审,反馈:\n{iterations[-1]['feedback']}"
            exec_result = await self._run_member(self.executor, prompt, ctx)
            current_output = exec_result.output
            eval_prompt = f'评估任务结果。\n任务: {task}\n结果:\n{current_output}\n只输出 JSON: {{"pass": true/false, "score": 0.0-1.0, "feedback": "..."}}'
            eval_result = await self._run_member(self.evaluator, eval_prompt, ctx)
            flag, score, feedback = self._parse_eval(eval_result.output)
            iterations.append({"iteration": i, "output": current_output, "score": score, "passed": flag, "feedback": feedback})
            if flag:
                passed = True
                break
        return AgentResult(output=current_output, extra={"topology": "reflection", "iterations": iterations, "passed": passed})

    def _parse_eval(self, text):
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                passed = bool(data.get("pass", False))
                score = data.get("score")
                if isinstance(score, (int, float)):
                    score = float(score)
                    if score >= self.pass_threshold:
                        passed = True
                return passed, score, str(data.get("feedback", ""))
            except json.JSONDecodeError:
                pass
        lower = text.lower()
        return any(k in lower for k in ["pass", "通过"]), None, text[:300]
