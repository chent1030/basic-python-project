"""Demo:一次性触发(trigger)—— deepagents + agentscope 两后端对比。

最简单的用法:调 agent_gateway.trigger(agent 名, 消息),拿结果。
本 demo 同时跑同一任务的两个后端实现:
  - researcher_da(deepagents:自带规划/文件系统/工具循环)
  - researcher_as(agentscope:ReAct 工具循环)
对比两者的输出风格与耗时(在 result.extra.tracing 里)。

运行(需配好 LLM provider):
    python -m app.ai.demo.trigger_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

# 同一任务的两个后端实现(配置见 app/ai/agents/researcher_da 与 researcher_as)
PAIR = [
    ("deepagents", "researcher_da"),
    ("agentscope", "researcher_as"),
]


async def run_one(backend: str, agent: str, message: str) -> None:
    result = await agent_gateway.trigger(agent, message, source="api")
    tracing = result.extra.get("tracing", {})
    print(f"\n=== [{backend}] {agent} (耗时 {tracing.get('duration_ms', '?')}ms) ===")
    print(result.output)
    print(f"(tokens={result.tokens})")


async def main() -> None:
    await agent_gateway.startup()  # 加载 registry + llm + 数据源(必须先调)
    try:
        message = "用 200 字介绍 LangChain 的核心概念"
        print(f"任务: {message}")
        for backend, agent in PAIR:
            try:
                await run_one(backend, agent, message)
            except Exception as e:
                print(f"\n=== [{backend}] {agent} 失败: {e}")
    finally:
        await agent_gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
