"""deepagents 后端适配器。

把统一抽象翻译成 deepagents(基于 LangChain/LangGraph):
- 模型:复用 app.services.llm 已构建的 ChatOpenAI(同一 provider 真相源)
- 工具:ToolDef → LangChain @tool 包裹的函数(deepagents 接受 callable | BaseTool)
- 消息:list[dict] → deepagents 期望的 {"messages": [...]} 输入
- 输出:state dict 的 messages 末尾 → AgentResult.output
- 上下文记忆:依赖 deepagents 原生(checkpointer + thread_id),本适配器不重建

deepagents 的特有能力(subagents / filesystem / planning)在 single 拓扑里默认启用
(create_deep_agent 自带),subagent 拓扑由 SubagentRunner 单独处理。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from app.ai.base import AgentResult, BaseBackend
from app.ai.config import AgentConfig
from app.ai.tools import ToolDef, resolve_tools
from app.core.logging_config import get_logger

log = get_logger("app.ai.backend.deepagents")


def _to_lc_tool(tdef: ToolDef) -> Any:
    """把 ToolDef 包成 LangChain BaseTool(deepagents 接受)。

    用 langchain_core 的 @tool 装饰器包裹原始函数 —— 它会从函数签名 + type hints
    自动推断 args_schema(参数名/类型/必填),这是 StructuredTool.from_function
    做不到的(后者不推断 schema,导致 LLM 调用时不传参数)。
    装饰后用 tdef.name/description 覆盖名字与描述(支持自定义工具名)。
    """
    from langchain_core.tools import tool as lc_tool

    tool_obj = lc_tool(tdef.func)
    # 覆盖名字/描述为 ToolDef 里登记的(可能与函数名不同)
    tool_obj.name = tdef.name
    if tdef.description:
        tool_obj.description = tdef.description
    return tool_obj


def _messages_to_lc(messages: list[dict]) -> list[BaseMessage]:
    """dict 消息 → LangChain message 对象。"""
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            from langchain_core.messages import SystemMessage

            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            from langchain_core.messages import HumanMessage

            out.append(HumanMessage(content=content))
    return out


def _extract_output(result: Any) -> tuple[str, int | None]:
    """从 deepagents 的 invoke 结果里取输出文本 + token(若有)。"""
    messages = None
    if isinstance(result, dict):
        messages = result.get("messages")
    if messages:
        last = messages[-1]
        content = getattr(last, "content", str(last))
        if isinstance(content, list):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        # token 用量(若 metadata 里有)
        tokens = None
        md = getattr(last, "usage_metadata", None) or {}
        if isinstance(md, dict):
            tokens = md.get("total_tokens")
        return str(content), tokens
    return str(result), None


class DeepAgentsBackend(BaseBackend):
    """deepagents 后端。惰性构建 agent(首次 invoke 时建,缓存复用)。"""

    backend_name = "deepagents"

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self._agent: Any = None  # CompiledStateGraph

    def _get_chat_model(self):
        """复用 app.services.llm 的 provider ChatOpenAI。"""
        from app.services.llm import llm as llm_svc

        provider = self.cfg.provider or None
        model = llm_svc._get_model(provider)  # 已构建好的 BaseChatModel
        # 覆盖 model/temperature(若 config 指定)
        binds: dict[str, Any] = {}
        if self.cfg.model:
            binds["model"] = self.cfg.model
        if self.cfg.temperature is not None:
            binds["temperature"] = self.cfg.temperature
        return model.bind(**binds) if binds else model

    def _ensure_agent(self) -> Any:
        if self._agent is None:
            from deepagents import create_deep_agent

            chat_model = self._get_chat_model()
            tools = [_to_lc_tool(t) for t in resolve_tools(self.cfg.tools)]
            self._agent = create_deep_agent(
                model=chat_model,
                tools=tools,
                system_prompt=self.cfg.system_prompt or "You are a helpful assistant.",
            )
            log.info("deepagents agent 已构建(tools=%d)", len(tools))
        return self._agent

    async def invoke(self, ctx) -> AgentResult:
        agent = self._ensure_agent()
        result = await agent.ainvoke(
            {"messages": _messages_to_lc(ctx.messages)},
            config={"recursion_limit": self.cfg.recursion_limit},
        )
        output, tokens = _extract_output(result)
        return AgentResult(output=output, tokens=tokens)

    async def stream(self, ctx) -> AsyncIterator[str]:
        agent = self._ensure_agent()
        async for event in agent.astream(
            {"messages": _messages_to_lc(ctx.messages)},
            config={"recursion_limit": self.cfg.recursion_limit},
            stream_mode="messages",
        ):
            # stream_mode="messages" 产出 (message_chunk, metadata)
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
            elif isinstance(event, str):
                yield event


__all__ = ["DeepAgentsBackend"]
