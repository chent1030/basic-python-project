"""主 agent 委派子任务(deepagents 原生)。"""
from __future__ import annotations

from app.harness.base import BaseAgent
from app.harness.context import AgentResult


class BaseSubagentAgent(BaseAgent):
    """子 agent 委派拓扑。用 deepagents 原生 subagents + task 工具。"""
    subagents: list[type[BaseAgent]] = []
    backend: str = "deepagents"  # subagent 必须用 deepagents

    async def _execute_topology(self, ctx):
        from deepagents import create_deep_agent
        from langchain_core.messages import HumanMessage

        from app.services.llm import llm

        chat_model = llm._get_model(self.provider or None)
        main_tools = []
        subs = []
        for sa in self.subagents:
            inst = sa()
            sub_tools = []
            if inst.tools:
                from app.harness.backends.deepagents_backend import _to_lc_tool
                from app.harness.tools import resolve_tools
                sub_tools = [_to_lc_tool(t) for t in resolve_tools(inst.tools)]
            subs.append({
                "name": inst.name or sa.__name__,
                "description": inst.system_prompt[:200] or f"子agent {inst.name}",
                "system_prompt": inst.system_prompt or "You are a helpful assistant.",
                "tools": sub_tools,
                "model": llm._get_model(inst.provider or None),
            })
        # 构建并执行
        from langchain_core.messages import SystemMessage
        msgs = []
        if self.system_prompt:
            msgs.append(SystemMessage(content=self.system_prompt))
        for m in ctx.messages:
            if m["role"] == "user":
                msgs.append(HumanMessage(content=m["content"]))
        agent = create_deep_agent(
            model=chat_model, tools=main_tools,
            system_prompt=self.system_prompt or "You are a coordinator. Delegate subtasks as needed.",
            subagents=subs,
        )
        result = await agent.ainvoke({"messages": msgs}, config={"recursion_limit": self.recursion_limit})
        from app.harness.backends.deepagents_backend import _extract_output
        text, _ = _extract_output(result)
        return AgentResult(output=text, extra={"topology": "subagent"})
