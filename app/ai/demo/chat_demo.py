"""Demo:持续对话(chat)—— deepagents + agentscope 两后端对比。

chat 模式按 session_id 维护历史(会话记忆中间件自动 load/append)。
本 demo 对两后端各跑一段两轮对话,验证两者都能记住上文:
  - support_bot_da(deepagents)
  - support_bot_as(agentscope)

运行(需配好 LLM provider + 数据源):
    python -m app.ai.demo.chat_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

PAIR = [
    ("deepagents", "support_bot_da"),
    ("agentscope", "support_bot_as"),
]


async def run_dialog(backend: str, agent: str) -> None:
    session_id = f"demo-{agent}"
    print(f"\n=== [{backend}] {agent} ===")
    r1 = await agent_gateway.chat(agent, session_id, "我昨天下的单子还没到,单号 A123")
    print("第1轮:", r1.output)
    r2 = await agent_gateway.chat(agent, session_id, "那现在到哪了?")
    print("第2轮:", r2.output)
    print("(两后端都应基于会话历史记住单号 A123)")


async def main() -> None:
    await agent_gateway.startup()
    try:
        for backend, agent in PAIR:
            try:
                await run_dialog(backend, agent)
            except Exception as e:
                print(f"\n=== [{backend}] {agent} 失败: {e}")
    finally:
        await agent_gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
