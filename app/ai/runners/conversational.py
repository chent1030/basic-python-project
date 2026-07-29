"""conversational 拓扑运行器 —— 多 agent 群聊讨论(agentscope MsgHub 思路)。

成员轮流发言 rounds 轮:每轮按 members 顺序,每个成员把前一个成员的发言当作输入回应,
形成多 agent 讨论。最终输出 = 最后一轮最后一个成员的发言。

每个成员通过 run_member 回调跑(走完整 run 流程 → 树状记录)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.conversational")


class ConversationalRunner(BaseRunner):
    topology = "conversational"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        message = ctx.last_user_message
        members = self.cfg.members
        if not members:
            return AgentResult(output="", extra={"topology": "conversational"})

        transcript: list[tuple[str, str]] = []
        current = message
        last_output = message
        for r in range(self.cfg.rounds):
            for name in members:
                # 把话题 + 上一条发言组合,让成员接续讨论
                if r == 0 and name == members[0]:
                    prompt = current
                else:
                    prev = transcript[-1][0] if transcript else "主持人"
                    prompt = (
                        f"讨论话题: {message}\n"
                        f"上一条({prev}): {last_output}\n请给出你的观点。"
                    )
                result = await run_member(name, prompt, ctx)
                last_output = result.output
                transcript.append((name, last_output))
                current = last_output

        return AgentResult(
            output=last_output,
            extra={"topology": "conversational", "transcript": transcript},
        )


__all__ = ["ConversationalRunner"]
