"""运行器注册表 —— topology 名 -> Runner 类。

gateway._get_runner() 用 pick_runner(topology) 选 runner。
复合 runner(parallel/sequential/conversational/subagent/router)各实现自己的编排,
内部通过 gateway 提供的 run_member 回调跑成员 agent(走完整 run 流程,树状记录)。
"""
from __future__ import annotations

from app.ai.runners.base import BaseRunner


def pick_runner(topology: str) -> type[BaseRunner]:
    """按拓扑名返回 runner 类。延迟 import,避免循环依赖。"""
    if topology == "single":
        from app.ai.runners.single import SingleRunner

        return SingleRunner
    if topology == "subagent":
        from app.ai.runners.subagent import SubagentRunner

        return SubagentRunner
    if topology == "parallel":
        from app.ai.runners.parallel import ParallelRunner

        return ParallelRunner
    if topology == "sequential":
        from app.ai.runners.sequential import SequentialRunner

        return SequentialRunner
    if topology == "conversational":
        from app.ai.runners.conversational import ConversationalRunner

        return ConversationalRunner
    if topology == "router":
        from app.ai.runners.router import RouterRunner

        return RouterRunner
    raise ValueError(
        f"未知 topology: {topology}"
        f"(single | subagent | parallel | sequential | conversational | router)"
    )


__all__ = ["pick_runner", "BaseRunner"]
