"""DeepAgents 后端 —— 工具循环/规划/文件系统/skills。

从 BaseAgent 的类属性(provider/system_prompt/tools/skills)构建 deepagents agent,
跑工具循环,返回输出。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.harness.backends.base import BaseBackend
from app.harness.context import AgentRunContext

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


def _messages_to_lc(messages: list[dict]) -> list:
    """dict 消息 → LangChain message 对象。"""
    out = []
    for m in messages:
        role, content = m.get("role", "user"), m.get("content", "")
        if role == "system":
            from langchain_core.messages import SystemMessage

            out.append(SystemMessage(content=content))
        elif role == "assistant":
            from langchain_core.messages import AIMessage

            out.append(AIMessage(content=content))
        else:
            from langchain_core.messages import HumanMessage

            out.append(HumanMessage(content=content))
    return out


def _extract_output(result: Any) -> tuple[str, int | None]:
    """从 deepagents invoke 结果取输出文本 + token。"""
    messages = result.get("messages") if isinstance(result, dict) else None
    if messages:
        last = messages[-1]
        content = getattr(last, "content", str(last))
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        tokens = None
        md = getattr(last, "usage_metadata", None) or {}
        if isinstance(md, dict):
            tokens = md.get("total_tokens")
        return str(content), tokens
    return str(result), None


class DeepAgentsBackend(BaseBackend):
    """deepagents 后端。惰性构建 agent(首次 invoke 时建,缓存)。"""

    def __init__(self, agent: BaseAgent) -> None:
        self.agent = agent
        self._agent_obj: Any = None

    def _get_chat_model(self):
        from app.services.llm import llm

        model = llm._get_model(self.agent.provider or None)
        binds: dict[str, Any] = {}
        if self.agent.model:
            binds["model"] = self.agent.model
        if self.agent.temperature is not None:
            binds["temperature"] = self.agent.temperature
        return model.bind(**binds) if binds else model

    def _ensure_agent(self):
        if self._agent_obj is None:
            from deepagents import create_deep_agent

            from app.harness.tools import resolve_tools

            chat_model = self._get_chat_model()
            tool_defs = resolve_tools(self.agent.tools) if self.agent.tools else []
            tools = [_to_lc_tool(t) for t in tool_defs]
            kwargs: dict[str, Any] = dict(
                model=chat_model,
                tools=tools,
                system_prompt=self.agent.system_prompt
                or "You are a helpful assistant.",
            )
            if self.agent.skills:
                kwargs["skills"] = self._resolve_skill_paths()
            self._agent_obj = create_deep_agent(**kwargs)
        return self._agent_obj

    def _resolve_skill_paths(self) -> list[str]:
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        return [str(Path(s) if Path(s).is_absolute() else root / s) for s in self.agent.skills]

    async def invoke(self, ctx: AgentRunContext) -> str:
        agent = self._ensure_agent()
        result = await agent.ainvoke(
            {"messages": _messages_to_lc(ctx.messages)},
            config={"recursion_limit": self.agent.recursion_limit},
        )
        text, _ = _extract_output(result)
        return text

    async def stream(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        agent = self._ensure_agent()
        async for event in agent.astream(
            {"messages": _messages_to_lc(ctx.messages)},
            config={"recursion_limit": self.agent.recursion_limit},
            stream_mode="messages",
        ):
            if isinstance(event, tuple) and event:
                chunk = event[0]
                text = getattr(chunk, "content", "")
                if isinstance(text, list):
                    text = "".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in text
                    )
                if text:
                    yield str(text)


def _to_lc_tool(tdef: Any) -> Any:
    """ToolDef → LangChain BaseTool(用 @tool 装饰器自动推断 schema)。"""
    from langchain_core.tools import tool as lc_tool

    tool_obj = lc_tool(tdef.func)
    tool_obj.name = tdef.name
    if tdef.description:
        tool_obj.description = tdef.description
    return tool_obj


__all__ = ["DeepAgentsBackend"]
