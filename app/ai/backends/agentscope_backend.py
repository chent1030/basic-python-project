"""agentscope 后端适配器。

把统一抽象翻译成 AgentScope 2.0:
- 模型:把 provider 配置翻译成 OpenAIChatModel(走 OpenAI 兼容端点 = NewAPI)
        (providers 都是 OpenAI 兼容,统一用 OpenAIChatModel)
- 工具:ToolDef → agentscope Tool(惰性转换)
- 消息:dict → UserMsg/AssistantMsg
- 输出:Msg.get_text_content() → AgentResult.output
- 上下文记忆:依赖 agentscope 原生 memory(agent 自带对话内记忆),本适配器不重建

agent.reply 是 async;reply_stream 是 async generator,从 TextBlockDeltaEvent 取 delta。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AgentResult, BaseBackend
from app.ai.config import AgentConfig
from app.ai.tools import ToolDef, resolve_tools
from app.core.logging_config import get_logger

log = get_logger("app.ai.backend.agentscope")


def _to_agentscope_tool(tdef: ToolDef) -> Any:
    """把 ToolDef 包成 agentscope 2.0 的 FunctionTool。

    agentscope 2.0 用 FunctionTool(func, name, description) 把普通函数包成工具
    (async/同步均可),再用 Toolkit.add_tool 加入。

    重要:agentscope 默认对「非只读」工具触发权限确认(RequireUserConfirmEvent,
    等用户确认才执行),这在无交互的自动化场景会让 agent 卡住。
    我们的框架已有自己的中间件(filter)做安全控制,不依赖 agentscope 的 HITL,
    故这里统一设 is_read_only=True,走 agentscope 的只读 fast path(自动 ALLOW)。
    """
    from agentscope.tool import FunctionTool

    return FunctionTool(
        func=tdef.func,
        name=tdef.name,
        description=tdef.description or tdef.name,
        is_read_only=True,
    )


def _to_agentscope_messages(messages: list[dict]) -> list[Any]:
    """dict 消息 → agentscope Msg 列表。

    agentscope Agent.reply 接受 Msg 或 list[Msg]。历史消息转成对应类型。
    """
    from agentscope.message import AssistantMsg, SystemMsg, UserMsg

    out: list[Any] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMsg(name="system", content=content))
        elif role == "assistant":
            out.append(AssistantMsg(name="assistant", content=content))
        else:
            out.append(UserMsg(name="user", content=content))
    return out


class AgentScopeBackend(BaseBackend):
    """agentscope 后端。惰性构建 agent(首次 invoke 时建,缓存复用)。"""

    backend_name = "agentscope"

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self._agent: Any = None

    def _build_model(self) -> Any:
        """把 provider 配置翻译成 agentscope 的 OpenAIChatModel。

        复用 llm.providers 的配置(base_url/api_key/model),统一走 OpenAI 兼容端点。
        """
        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel

        from app.core.config import settings
        from app.services.llm import llm as llm_svc

        provider = self.cfg.provider or llm_svc._resolve_provider(None)
        pcfg = settings.llm.providers[provider]
        credential = OpenAICredential(api_key=pcfg.api_key, base_url=pcfg.base_url)
        temp = (
            self.cfg.temperature
            if self.cfg.temperature is not None
            else pcfg.temperature
        )
        parameters = OpenAIChatModel.Parameters(
            temperature=temp,
            max_tokens=self.cfg.max_tokens,
        )
        return OpenAIChatModel(
            credential=credential,
            model=self.cfg.model or pcfg.model,
            parameters=parameters,
            stream=False,
        )

    def _ensure_agent(self) -> Any:
        if self._agent is None:
            from agentscope.agent import Agent
            from agentscope.tool import Toolkit

            model = self._build_model()
            tool_defs = resolve_tools(self.cfg.tools)
            # agentscope Toolkit 接受 ToolBase 列表;用 register_tool 包裹
            toolkit = None
            if tool_defs:
                try:
                    tools = [_to_agentscope_tool(t) for t in tool_defs]
                    toolkit = Toolkit(tools=tools)  # type: ignore[arg-type]
                except Exception:
                    log.exception("构建 agentscope Toolkit 失败,该 agent 将无工具")
                    toolkit = None
            self._agent = Agent(
                name=self.cfg.backend + "_agent",
                system_prompt=self.cfg.system_prompt or "You are a helpful assistant.",
                model=model,
                toolkit=toolkit,
            )
            log.info("agentscope agent 已构建(tools=%d)", len(tool_defs))
        return self._agent

    async def invoke(self, ctx) -> AgentResult:
        agent = self._ensure_agent()
        msgs = _to_agentscope_messages(ctx.messages)
        # reply 接受最后一条消息(或列表);历史用列表传入,agentscope 内部维护上下文
        result = await agent.reply(msgs[-1] if msgs else None)
        text = ""
        tokens = None
        if result is not None:
            text = result.get_text_content() or ""
            usage = getattr(result, "usage", None)
            if isinstance(usage, dict):
                tokens = usage.get("total_tokens")
        return AgentResult(output=text, tokens=tokens)

    async def stream(self, ctx) -> AsyncIterator[str]:
        agent = self._ensure_agent()
        msgs = _to_agentscope_messages(ctx.messages)
        from agentscope.event import TextBlockDeltaEvent

        async for ev in agent.reply_stream(msgs[-1] if msgs else None, yield_final_msg=True):
            if isinstance(ev, TextBlockDeltaEvent):
                delta = getattr(ev, "delta", None)
                if delta:
                    yield str(delta)


__all__ = ["AgentScopeBackend"]
