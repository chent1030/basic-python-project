"""Demo:6 种拓扑 —— deepagents + agentscope 两后端全覆盖。

框架支持 6 种拓扑,每种都提供两后端实现(可对比):
  single          deepagents:researcher_da / agentscope:researcher_as
  subagent        deepagents 原生:research_team_da(主 agent 委派子任务)
  parallel        deepagents:review_squad_da / agentscope:review_squad_as
  sequential      deepagents:content_pipeline_da / agentscope:content_pipeline
  conversational  deepagents:debate_room_da / agentscope:debate_room
  router          自建(与后端无关):dispatcher

注:agentscope 2.0 无原生 subagent 委派,故「研究小组」在 agentscope 侧用
   sequential 流水线(research_team_as)等价表达同一业务目标。

调用方式都一样:agent_gateway.trigger(agent 名, 消息)。

运行(需配好 LLM provider):
    python -m app.ai.demo.topology_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

# (topology, deepagents agent, agentscope agent 或等价拓扑, 演示输入)
DEMOS = [
    ("single", "researcher_da", "researcher_as", "介绍向量数据库的原理"),
    ("subagent/seq", "research_team_da", "research_team_as",
     "研究 2026 年值得关注的 AI Agent 框架"),
    ("sequential", "content_pipeline_da", "content_pipeline",
     "LangChain is a framework for LLM apps."),
    ("parallel", "review_squad_da", "review_squad_as",
     "用 RAG 提升客服回答质量,可行吗?"),
    ("conversational", "debate_room_da", "debate_room",
     "AI 是否会取代大部分白领工作?"),
    # router 与后端无关,两列共用同一个 dispatcher
    ("router", "dispatcher", "dispatcher", "帮我查一下订单 A123 的状态"),
]


async def run_one(backend: str, agent: str, message: str) -> None:
    try:
        result = await agent_gateway.trigger(agent, message, source="api")
        out = result.output[:300] + ("..." if len(result.output) > 300 else "")
        print(f"  [{backend}] {agent}: {out}")
    except Exception as e:
        print(f"  [{backend}] {agent} 失败: {e}")


async def main() -> None:
    await agent_gateway.startup()
    try:
        for topology, da, as_, message in DEMOS:
            print(f"\n=== [{topology}] 输入: {message} ===")
            await run_one("deepagents", da, message)
            if da != as_:  # router 共用,不重复
                await run_one("agentscope", as_, message)
    finally:
        await agent_gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
