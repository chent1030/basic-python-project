"""多 agent 群聊讨论。"""
from __future__ import annotations

from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BaseConversationalAgent(BaseAgent):
    members: list[type[BaseAgent]] = []
    rounds: int = 3

    async def _execute_topology(self, ctx):
        message = ctx.last_user_message
        last_output = message
        transcript = []
        for r in range(self.rounds):
            for m in self.members:
                prompt = message if r == 0 and m == self.members[0] else (
                    f"讨论话题: {message}\n"
                    f"上一条: {last_output}\n"
                    f"请给出你的观点。"
                )
                result = await self._run_member(m, prompt, ctx)
                last_output = result.output
                transcript.append((m.name or m.__name__, last_output))
        return AgentResult(
            output=last_output,
            extra={"topology": "conversational", "transcript": transcript},
        )
