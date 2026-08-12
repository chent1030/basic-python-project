"""AgentScope 后端 —— ReAct 循环/skills。

从 BaseAgent 类属性构建 agentscope Agent,跑 ReAct 工具循环。
is_read_only=True 避免权限确认卡住(框架自己的 filter 中间件管安全)。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.harness.backends.base import BaseBackend
from app.harness.context import AgentRunContext

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


def _to_messages(messages: list[dict]) -> list:
    """dict → agentscope Msg 列表。"""
    from agentscope.message import AssistantMsg, SystemMsg, UserMsg

    out = []
    for m in messages:
        role, content = m.get("role", "user"), m.get("content", "")
        if role == "system":
            out.append(SystemMsg(name="system", content=content))
        elif role == "assistant":
            out.append(AssistantMsg(name="assistant", content=content))
        else:
            out.append(UserMsg(name="user", content=content))
    return out


class AgentScopeBackend(BaseBackend):
    """agentscope 后端。惰性构建 agent(首次 invoke 时建,缓存)。"""

    def __init__(self, agent: BaseAgent) -> None:
        self.agent = agent
        self._agent_obj: Any = None

    def _build_model(self):
        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel

        from app.core.config import settings
        from app.services.llm import llm

        provider = self.agent.provider or llm._resolve_provider(None)
        pcfg = settings.llm.providers[provider]
        credential = OpenAICredential(api_key=pcfg.api_key, base_url=pcfg.base_url)
        temp = (
            self.agent.temperature
            if self.agent.temperature is not None
            else pcfg.temperature
        )
        parameters = OpenAIChatModel.Parameters(
            temperature=temp, max_tokens=self.agent.max_tokens
        )
        return OpenAIChatModel(
            credential=credential,
            model=self.agent.model or pcfg.model,
            parameters=parameters,
            stream=False,
        )

    def _ensure_agent(self):
        if self._agent_obj is None:
            from agentscope.agent import Agent
            from agentscope.tool import Toolkit

            model = self._build_model()
            toolkit = None
            if self.agent.tools:
                from app.harness.tools import resolve_tools

                tool_defs = resolve_tools(self.agent.tools)
                if tool_defs:
                    tools = [_to_as_tool(t) for t in tool_defs]
                    toolkit = Toolkit(tools=tools)
            self._agent_obj = Agent(
                name=self.agent.name or "agent",
                system_prompt=self.agent.system_prompt
                or "You are a helpful assistant.",
                model=model,
                toolkit=toolkit,
            )
        return self._agent_obj

    async def invoke(self, ctx: AgentRunContext) -> str:
        agent = self._ensure_agent()
        msgs = _to_messages(ctx.messages)
        result = await agent.reply(msgs[-1] if msgs else None)
        return result.get_text_content() if result else ""

    async def stream(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        agent = self._ensure_agent()
        msgs = _to_messages(ctx.messages)
        from agentscope.event import TextBlockDeltaEvent

        async for ev in agent.reply_stream(msgs[-1] if msgs else None):
            if isinstance(ev, TextBlockDeltaEvent) and ev.delta:
                yield str(ev.delta)


def _to_as_tool(tdef: Any) -> Any:
    """ToolDef → agentscope FunctionTool。"""
    from agentscope.tool import FunctionTool

    return FunctionTool(
        func=tdef.func,
        name=tdef.name,
        description=tdef.description or tdef.name,
        is_read_only=True,
    )


__all__ = ["AgentScopeBackend"]
