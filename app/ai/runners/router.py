"""router 拓扑运行器 —— supervisor 按意图分发(自建)。

用 LLM 把用户输入分类到 config.routes 的某个意图,再路由到对应成员 agent。
路由表:routes = {意图名: agent 名}

流程:
1. 构造分类 prompt(列出所有意图),用 provider 模型判定意图
2. 在 routes 表里查意图 -> agent 名
3. 把输入转发给该 agent(走 run_member,树状记录)
4. 找不到意图时落到默认路由(可选:_default 键,否则直接报错或取第一个)

自建 supervisor,不依赖特定库的 router 模式,跨后端通用。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.runners.base import BaseRunner
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext

log = get_logger("app.ai.runner.router")


class RouterRunner(BaseRunner):
    topology = "router"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    async def _classify(self, message: str) -> str:
        """用 LLM 把消息分类到 routes 的某个意图键。"""
        from app.services.llm import llm as llm_svc

        intents = list(self.cfg.routes.keys())
        prompt = (
            f"把下面的用户消息分类到这些意图之一,只输出意图名,不要其它文字。\n"
            f"可选意图: {', '.join(intents)}\n"
            f"用户消息: {message}\n意图:"
        )
        from langchain_core.messages import HumanMessage

        text = await llm_svc.invoke(
            [HumanMessage(content=prompt)],
            provider=self.cfg.provider or None,
        )
        # 提取意图名(容忍模型多输出文字)
        text = text.strip().lower()
        for intent in intents:
            if intent.lower() in text:
                return intent
        # 兜底:整体匹配
        m = re.match(r"[a-zA-Z_]+", text)
        return m.group(0) if m else ""

    async def run(self, ctx: AgentRunContext, run_member) -> AgentResult:
        message = ctx.last_user_message
        intent = await self._classify(message)
        agent_name = self.cfg.routes.get(intent)
        if agent_name is None:
            # 兜底:默认路由或第一个
            agent_name = self.cfg.routes.get("_default") or next(iter(self.cfg.routes.values()))
            log.info("router 未命中意图 '%s',降级到 '%s'", intent, agent_name)
        else:
            log.info("router 路由: 意图=%s -> agent=%s", intent, agent_name)

        result = await run_member(agent_name, message, ctx)
        result.extra["topology"] = "router"
        result.extra["router"] = {"intent": intent, "target": agent_name}
        return result


__all__ = ["RouterRunner"]
