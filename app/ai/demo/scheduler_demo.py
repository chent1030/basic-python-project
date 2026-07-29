"""Demo:定时任务触发 agent —— deepagents + agentscope 两后端。

trigger 不止来自 API:scheduler 定时任务也能触发,运行记录 source='scheduler'。
本 demo 用两后端各触发一次,展示定时任务里调 agent_gateway 的写法。

运行(查看效果,需配好 provider):
    python -m app.ai.demo.scheduler_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

PAIR = [
    ("deepagents", "researcher_da"),
    ("agentscope", "researcher_as"),
]


async def main() -> None:
    # 这就是定时任务里会执行的逻辑(见 app/tasks/agent_tasks.py)
    for backend, agent in PAIR:
        print(f"\n=== [{backend}] {agent} (source=scheduler) ===")
        try:
            result = await agent_gateway.trigger(
                agent, "今日 AI 领域值得关注的技术动态", source="scheduler"
            )
            print(result.output[:300])
        except Exception as e:
            print(f"失败: {e}")

    # 查看运行记录里 source 字段,确认是 scheduler 触发的
    from app.ai.runs import run_store

    for _, agent in PAIR:
        runs = await run_store.list_runs(agent, limit=1)
        if runs:
            print(f"\n{agent} 最近一条运行: source={runs[0]['trigger_source']}")


if __name__ == "__main__":
    asyncio.run(main())
