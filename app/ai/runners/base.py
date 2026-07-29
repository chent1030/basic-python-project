"""运行器基类 —— 每种拓扑一个 runner。

runner 负责把一次 agent 运行按其拓扑编排:
- single:    单后端直接跑
- subagent:  主 agent + 子 agent 委派(deepagents)
- parallel:  成员 agent 并行跑同输入,汇总(agentscope Fanout 思路,自实现)
- sequential:成员 agent 串成流水线,前者输出喂后者(自实现)
- conversational: 成员 agent 群聊 N 轮(自实现,MsgHub 思路)
- router:    LLM 按意图分类 → 选成员 agent(自建 supervisor)

runner 只管「怎么编排成员」,不管中间件/记录/日志 —— 那些是 gateway.run() 的职责。
runner.run() 接收一个「单 agent 执行回调」(gateway 提供),用它跑成员 agent,
从而让成员 agent 调用也走完整的 run 流程(树状记录 + 中间件)。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.ai.base import AgentResult

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

# 成员执行回调:gateway 提供,把成员 agent 名 + 输入消息 → AgentResult
# gateway 会用它在完整 run 流程里跑成员(带 parent_run_id 形成树)
MemberRunner = Callable[[str, str, "AgentRunContext"], Awaitable[AgentResult]]


class BaseRunner:
    """所有拓扑 runner 的基类。"""

    topology: str = "base"

    async def run(self, ctx: AgentRunContext, run_member: MemberRunner) -> AgentResult:
        """编排本次运行。子类实现。

        Args:
            ctx:         本次运行的上下文(顶层 agent 的)
            run_member:  gateway 提供的成员执行回调
                         run_member(name, message_text, parent_ctx) -> AgentResult
        """
        raise NotImplementedError


__all__ = ["BaseRunner", "MemberRunner"]
