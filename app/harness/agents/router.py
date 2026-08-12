"""supervisor 按意图分发。"""
from __future__ import annotations

from app.harness.base import BaseAgent


class BaseRouterAgent(BaseAgent):
    routes: dict[str, type[BaseAgent]] = {}

    async def _classify(self, message: str) -> str:
        from langchain_core.messages import HumanMessage

        from app.services.llm import llm
        intents = list(self.routes.keys())
        prompt = f"把消息分类到这些意图之一,只输出意图名: {', '.join(intents)}\n消息: {message}\n意图:"
        text = await llm.invoke([HumanMessage(content=prompt)], provider=self.provider or None)
        text = text.strip().lower()
        for intent in intents:
            if intent.lower() in text:
                return intent
        return ""

    async def _execute_topology(self, ctx):
        message = ctx.last_user_message
        intent = await self._classify(message)
        agent_cls = self.routes.get(intent)
        if agent_cls is None:
            agent_cls = self.routes.get("_default") or list(self.routes.values())[0]
        result = await self._run_member(agent_cls, message, ctx)
        result.extra["topology"] = "router"
        result.extra["router"] = {"intent": intent, "target": agent_cls.name or agent_cls.__name__}
        return result
