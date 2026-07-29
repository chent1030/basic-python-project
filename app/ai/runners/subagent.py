"""subagent 拓扑运行器 —— 主 agent 委派子任务(deepagents 原生)。

利用 deepagents 的 subagents 机制:主 agent 通过内置 task 工具把子任务委派给
隔离上下文的子 agent,子 agent 自主跑完返回结果。

子 agent 定义来自 config.subagents(引用其它 agent 目录名):
- 取每个子 agent 的 system_prompt + provider + tools 构造成 deepagents SubAgent TypedDict
- 主 agent(deepagents)拿到这些 subagent 后,运行时自己决定何时委派

成员子 agent 的运行记录:deepagents 内部跑子 agent 时无法回调我们的 run_member
(它在自己进程内跑),所以子 agent 不单独写 agent_runs 树节点;
主 agent 的运行记录会包含完整输出。如需子 agent 级追踪,可用 deepagents 的 stream.subagents。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.ai.tools import resolve_tools
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.subagent")


class SubagentRunner(BaseRunner):
    topology = "subagent"

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._agent = None  # 惰性构建的 deepagents CompiledStateGraph

    def _build_subagents(self) -> list[dict]:
        """把 config.subagents(引用的 agent 名)转成 deepagents SubAgent dict。"""
        from app.ai.registry import registry

        subs: list[dict] = []
        for name in self.cfg.subagents:
            try:
                sub_cfg = registry.get(name)
                # 子 agent 只取 single 语义的部分(provider/prompt/tools)
                tools = [_lc_tool(t) for t in resolve_tools(sub_cfg.tools, exclusive_agent=name)]
                provider = sub_cfg.provider or None
                subs.append(
                    {
                        "name": name,
                        "description": sub_cfg.system_prompt[:200]
                        or f"子 agent {name}",
                        "prompt": sub_cfg.system_prompt or "You are a helpful assistant.",
                        "tools": tools,
                        "model": _resolve_sub_model(provider),
                    }
                )
            except Exception:
                log.exception("构建子 agent '%s' 失败", name)
        return subs

    def _ensure_agent(self):
        if self._agent is None:
            from deepagents import create_deep_agent

            from app.services.llm import llm as llm_svc

            provider = self.cfg.provider or None
            chat_model = llm_svc._get_model(provider)
            main_tools = [_lc_tool(t) for t in resolve_tools(self.cfg.tools)]
            subs = self._build_subagents()
            self._agent = create_deep_agent(
                model=chat_model,
                tools=main_tools,
                system_prompt=self.cfg.system_prompt
                or "You are a coordinator. Delegate subtasks to subagents as needed.",
                subagents=subs,
            )
            log.info("subagent runner 已构建(主 agent + %d 子 agent)", len(subs))
        return self._agent

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        # subagent 用 deepagents 原生委派,不走 run_member(子 agent 在 deepagents 内部跑)
        from app.ai.backends.deepagents_backend import _extract_output, _messages_to_lc

        agent = self._ensure_agent()
        result = await agent.ainvoke(
            {"messages": _messages_to_lc(ctx.messages)},
            config={"recursion_limit": self.cfg.recursion_limit},
        )
        output, tokens = _extract_output(result)
        return AgentResult(output=output, tokens=tokens, extra={"topology": "subagent"})


def _lc_tool(tdef):
    """复用 deepagents backend 的工具转换。"""
    from app.ai.backends.deepagents_backend import _to_lc_tool

    return _to_lc_tool(tdef)


def _resolve_sub_model(provider):
    """子 agent 的 model:deepagents SubAgent 接受 str(如 'openai:gpt-4o')或 BaseChatModel。

    我们的 provider 是 OpenAI 兼容端点,deepagents 的字符串简写不适用,
    所以传 BaseChatModel 实例(复用 llm 的 provider ChatOpenAI)。
    """
    from app.services.llm import llm as llm_svc

    return llm_svc._get_model(provider or None)


__all__ = ["SubagentRunner"]
