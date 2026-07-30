"""pipeline 拓扑运行器 —— 顺序步骤中可嵌入并行步骤 + 自定义合并。

支持「A → [B,C,D 并行] → E」这种混合结构:
- 单 agent 步骤(run):顺序执行,输出喂下一步
- 并行步骤(parallel):多个 agent 同时跑(asyncio.gather),全部完成后用 aggregator 合并
- 合并方式由工程师自定义(在 agent 目录的 aggregator.py 用 @aggregator 定义)

每个成员 agent 通过 run_member 回调跑(走完整 run 流程 → 树状记录)。
并行阶段:后续步骤自动等待所有并行 agent 完成(asyncio.gather 本身就等)。

config.yml 示例(steps 显式声明,每步一对象):
    topology: pipeline
    steps:
      - name: step_a
        run: agent_a
      - name: step_bcd
        parallel: [b1, b2, b3]
        aggregator: merge          # 或自定义聚合器名
      - name: step_e
        run: agent_e
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.ai.aggregators import get_aggregator
from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.pipeline")


class PipelineRunner(BaseRunner):
    topology = "pipeline"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        if not self.cfg.steps:
            return AgentResult(output="", extra={"topology": "pipeline", "steps": []})

        # 每步的输出,按步骤名存(供 input_from 引用);也维护一个「上一步输出」游标
        step_outputs: dict[str, str] = {}
        current = ctx.last_user_message  # 流水线的初始输入
        trace: list[dict] = []           # 记录每步详情(给 extra)

        for step in self.cfg.steps:
            # 决定本步骤输入:显式 input_from > 上一步输出(current)
            step_input = step_outputs.get(step.input_from, current) if step.input_from else current

            if step.parallel:
                # ---- 并行步骤:多 agent 同时跑,等全部完成,再合并 ----
                result_text, member_outputs = await self._run_parallel(
                    step, step_input, ctx, run_member
                )
                trace.append({
                    "step": step.name or "parallel",
                    "kind": "parallel",
                    "members": step.parallel,
                    "aggregator": step.aggregator,
                    "outputs": member_outputs,
                })
            elif step.run:
                # ---- 单 agent 顺序步骤 ----
                result = await run_member(step.run, step_input, ctx)
                result_text = result.output
                trace.append({
                    "step": step.name or step.run,
                    "kind": "single",
                    "agent": step.run,
                })
            else:
                log.warning("pipeline 步骤 '%s' 既无 run 也无 parallel,跳过", step.name)
                continue

            # 记录本步输出(供后续 input_from 引用)+ 更新游标
            if step.name:
                step_outputs[step.name] = result_text
            current = result_text

        return AgentResult(
            output=current,
            extra={"topology": "pipeline", "steps": trace, "step_outputs": step_outputs},
        )

    async def _run_parallel(
        self, step, step_input: str, ctx: AgentRunContext, run_member
    ) -> tuple[str, list[tuple[str, str]]]:
        """并行执行 step.parallel 里的所有 agent,等全部完成后合并。

        Returns:
            (合并后的文本, [(agent名, 输出), ...] 按 members 顺序)
        """
        members = step.parallel
        log.info(
            "pipeline 并行步骤 '%s':%d 个 agent 同时执行 %s",
            step.name or "", len(members), members,
        )
        # asyncio.gather 等所有并行 agent 完成(任一失败不影响其它,记录为 ERROR)
        tasks = [run_member(name, step_input, ctx) for name in members]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        member_outputs: list[tuple[str, str]] = []
        for name, r in zip(members, results, strict=False):
            if isinstance(r, Exception):
                log.error("pipeline 并行成员 '%s' 失败: %s", name, r)
                member_outputs.append((name, f"[ERROR] {r}"))
            else:
                member_outputs.append((name, r.output))

        # 用聚合器合并(内置 merge/list/first 或自定义)
        agg = get_aggregator(step.aggregator)
        merged = agg(member_outputs)
        log.info(
            "pipeline 并行步骤 '%s' 全部完成,合并后 %d 字符",
            step.name or "", len(merged),
        )
        return merged, member_outputs


__all__ = ["PipelineRunner"]
