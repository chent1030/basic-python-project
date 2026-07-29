"""parallel 拓扑运行器 —— 多 agent 并行跑同输入,汇总结果(agentscope Fanout 思路)。

把同一输入分发给所有成员 agent 并行(asyncio.gather)执行,
按 aggregator 策略汇总:
- merge: 拼接所有输出(带成员名标注)
- list:  返回 JSON 列表 [{agent, output}, ...]
- first: 取第一个完成的结果

每个成员通过 run_member 回调跑(走完整 run 流程 → 树状记录)。
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.parallel")


class ParallelRunner(BaseRunner):
    topology = "parallel"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        message = ctx.last_user_message
        tasks = [run_member(name, message, ctx) for name in self.cfg.members]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs: list[tuple[str, str]] = []
        for name, r in zip(self.cfg.members, results, strict=False):
            if isinstance(r, Exception):
                log.error("parallel 成员 '%s' 失败: %s", name, r)
                outputs.append((name, f"[ERROR] {r}"))
            else:
                outputs.append((name, r.output))

        agg = self.cfg.aggregator
        if agg == "list":
            merged = json.dumps(
                [{"agent": n, "output": o} for n, o in outputs], ensure_ascii=False
            )
        elif agg == "first":
            merged = outputs[0][1] if outputs else ""
        else:  # merge(默认)
            merged = "\n\n".join(f"## {n}\n{o}" for n, o in outputs)

        return AgentResult(output=merged, extra={"topology": "parallel", "members": outputs})


__all__ = ["ParallelRunner"]
