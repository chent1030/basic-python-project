"""reflection 拓扑运行器 —— 执行 → 评估 → 不达标带反馈重试。

模式:
1. executor agent 产出结果
2. evaluator agent 评估(pass/fail + 分数 + 反馈)
3. pass=True → 返回;pass=False 且未达上限 → 把反馈拼进 prompt 重试
4. 达 max_iterations → 返回最后一次结果(标注未达标)

适合需要质量保证的任务:写代码 → 审查 → 不过则带反馈重写。

每个成员通过 run_member 回调跑(走完整 run 流程 → 树状记录)。

config.yml:
    topology: reflection
    executor: coder_agent       # 执行 agent
    evaluator: reviewer_agent   # 评估 agent
    max_iterations: 3
    pass_threshold: 0.8         # 评估分数 >= 此值算通过
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.reflection")


class ReflectionRunner(BaseRunner):
    topology = "reflection"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        executor = self.cfg.executor
        evaluator = self.cfg.evaluator
        if not executor or not evaluator:
            return AgentResult(
                output="[reflection 错误]需同时配置 executor 和 evaluator",
                extra={"topology": "reflection", "error": "missing executor/evaluator"},
            )

        task = ctx.last_user_message
        iterations: list[dict] = []
        current_output = ""
        passed = False

        for i in range(1, self.cfg.max_iterations + 1):
            # 1. 执行(executor)
            exec_prompt = task
            feedback = iterations[-1].get("feedback") if iterations else None
            if feedback:
                exec_prompt = (
                    f"{task}\n\n"
                    f"上次结果未通过评审,反馈如下,请据此改进:\n{feedback}"
                )
            exec_result = await run_member(executor, exec_prompt, ctx)
            current_output = exec_result.output

            # 2. 评估(evaluator):要求返回 JSON {pass, score, feedback}
            eval_prompt = (
                f"评估以下任务结果是否达标。\n"
                f"任务: {task}\n\n结果:\n{current_output}\n\n"
                f"只输出 JSON: {{\"pass\": bool, \"score\": 0-1, "
                f"\"feedback\": \"改进建议\"}}"
            )
            eval_result = await run_member(evaluator, eval_prompt, ctx)
            passed_flag, score, feedback = self._parse_eval(eval_result.output)

            iterations.append({
                "iteration": i,
                "output": current_output,
                "score": score,
                "passed": passed_flag,
                "feedback": feedback,
            })
            log.info(
                "reflection 第 %d 轮: score=%s passed=%s",
                i, score, passed_flag,
            )

            if passed_flag:
                passed = True
                break

        return AgentResult(
            output=current_output,
            extra={
                "topology": "reflection",
                "iterations": iterations,
                "passed": passed,
                "final_score": iterations[-1].get("score") if iterations else None,
                "total_iterations": len(iterations),
            },
        )

    def _parse_eval(self, text: str) -> tuple[bool, float | None, str]:
        """解析 evaluator 输出:(pass, score, feedback)。

        优先 JSON;失败则兜底(含"pass/通过"字样算 pass,feedback 取全文)。
        """
        json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                passed = bool(data.get("pass", False))
                score = data.get("score")
                if isinstance(score, (int, float)):
                    score = float(score)
                    # 也按阈值判断(评分 >= pass_threshold 算通过)
                    if score >= self.cfg.pass_threshold:
                        passed = True
                feedback = str(data.get("feedback", ""))
                return passed, score, feedback
            except json.JSONDecodeError:
                pass

        # 兜底:关键词判断
        lower = text.lower()
        passed = any(k in lower for k in ["pass", "通过", "合格", "达标", "good"])
        return passed, None, text[:300]


__all__ = ["ReflectionRunner"]
