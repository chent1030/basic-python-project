"""plan_execute 拓扑运行器 —— 先规划再逐步执行。

模式:planner agent 把任务拆成有序步骤列表(返回 JSON),
再逐步执行每个步骤(executor agent 或 planner 自己),前一步输出喂下一步。

与 sequential 的区别:sequential 的成员是预先固定的;plan_execute 的步骤是
planner 动态产出的(根据任务自适应)。

每个成员通过 run_member 回调跑(走完整 run 流程 → 树状记录)。

config.yml:
    topology: plan_execute
    planner: planner_agent       # 规划 agent(产出步骤 JSON)
    executor: task_executor      # 执行 agent;空=planner 自己执行
    max_steps: 10
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

log = get_logger("app.ai.runner.plan_execute")


class PlanExecuteRunner(BaseRunner):
    topology = "plan_execute"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        planner = self.cfg.planner
        if not planner:
            return AgentResult(
                output="[plan_execute 错误]未配置 planner",
                extra={"topology": "plan_execute", "error": "no planner"},
            )

        # 1. 规划:让 planner 把任务拆成步骤 JSON
        plan_prompt = (
            f"把以下任务拆解成可执行的步骤列表。"
            f"只输出 JSON,格式 {{\"steps\": [\"步骤1\", \"步骤2\", ...]}},不要其它文字。\n"
            f"任务: {ctx.last_user_message}"
        )
        plan_result = await run_member(planner, plan_prompt, ctx)
        steps = self._parse_steps(plan_result.output, max_steps=self.cfg.max_steps)
        log.info("plan_execute 规划出 %d 个步骤: %s", len(steps), steps)

        if not steps:
            # 解析失败兜底:planner 的输出当最终结果
            return AgentResult(
                output=plan_result.output,
                extra={
                    "topology": "plan_execute",
                    "plan": plan_result.output[:500],
                    "steps_executed": [],
                    "note": "规划未产出有效步骤,直接返回 planner 输出",
                },
            )

        # 2. 逐步执行
        executor = self.cfg.executor or planner
        current = ctx.last_user_message  # 执行链的当前输入
        trace: list[dict] = []

        for i, step in enumerate(steps, 1):
            exec_prompt = f"执行步骤 {i}/{len(steps)}:{step}\n\n上下文:{current}"
            result = await run_member(executor, exec_prompt, ctx)
            current = result.output
            trace.append({"step_index": i, "step": step, "output": current})
            log.info("plan_execute 步骤 %d/%d 完成", i, len(steps))

        return AgentResult(
            output=current,
            extra={
                "topology": "plan_execute",
                "plan": steps,
                "steps_executed": trace,
            },
        )

    @staticmethod
    def _parse_steps(text: str, *, max_steps: int = 10) -> list[str]:
        """从 planner 输出里解析步骤列表。

        优先解析 JSON {\"steps\": [...]};失败则按行/编号列表兜底。
        最多 max_steps 步(防失控)。
        """
        # 尝试提取 JSON(容忍前后多余文字 + ```json 代码块)
        json_match = re.search(r"\{[^{}]*\"steps\"[^{}]*\}", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                steps = data.get("steps", [])
                if isinstance(steps, list) and steps:
                    return [str(s) for s in steps[:max_steps]]
            except json.JSONDecodeError:
                pass

        # 兜底:按行解析(去掉空行、编号前缀)
        lines = []
        for line in text.strip().splitlines():
            line = re.sub(r"^\s*(\d+[\.\)、]|[-*•])\s*", "", line).strip()
            if line:
                lines.append(line)
        return lines[:max_steps]


__all__ = ["PlanExecuteRunner"]
