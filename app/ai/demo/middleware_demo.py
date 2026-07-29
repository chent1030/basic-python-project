"""Demo:中间件 + 4 类记忆 —— deepagents + agentscope 两后端对比。

四类记忆各自成独立中间件,在 agent config.yml 挂载,与后端解耦:
  context_memory(交后端原生) / session_memory(业务库) /
  persistent_memory(向量库) / external_memory(API)
本 demo 对两后端各触发一次,看中间件是否一致生效(result.extra.memory_sources):
  - middleware_demo_da(deepagents)
  - middleware_demo_as(agentscope)

运行(需配好 LLM provider;持久/外部记忆需额外配):
    python -m app.ai.demo.middleware_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

PAIR = [
    ("deepagents", "middleware_demo_da"),
    ("agentscope", "middleware_demo_as"),
]


async def main() -> None:
    await agent_gateway.startup()
    try:
        for backend, agent in PAIR:
            print(f"\n=== [{backend}] {agent} ===")
            try:
                result = await agent_gateway.trigger(agent, "记住:用户偏好中文回答", source="api")
                print("输出:", result.output[:200])
                print("memory_sources:", result.extra.get("memory_sources"))
                print("tracing:", result.extra.get("tracing"))
            except Exception as e:
                print(f"失败: {e}")
    finally:
        await agent_gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
