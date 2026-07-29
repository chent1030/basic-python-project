"""Demo:skill 集成 —— deepagents + agentscope 两后端对比。

skill 是「教 agent 方法论的指令文档」(SKILL.md),比 tool 更高层:
- tool:可执行函数(搜索、计算)—— LLM 调用拿返回值
- skill:指导文档(如何做代码评审)—— LLM 读说明后按步骤操作

本 demo 用 code_review skill(教 agent 结构化评审代码):
  - reviewer_da(deepagents:create_deep_agent(skills=[...]) 自动加载)
  - reviewer_as(agentscope:Toolkit(skills_or_loaders=[LocalSkillLoader(...)]))

加 skill 的方式:在 agent 的 config.yml 加 skills 字段(目录路径):
    skills:
      - skills/code_review

运行(需配好 LLM provider):
    python -m app.ai.demo.skill_demo
"""
from __future__ import annotations

import asyncio

from app.ai.gateway import agent_gateway

PAIR = [
    ("deepagents", "reviewer_da"),
    ("agentscope", "reviewer_as"),
]

# 一段故意有问题的代码,让 agent 用 code_review skill 评审
CODE = """\
def get_user(id):
    db = connect("mysql://root:pass@localhost/db")
    rows = db.query("SELECT * FROM users WHERE id = " + id)
    return rows[0]
"""


async def run_one(backend: str, agent: str) -> None:
    result = await agent_gateway.trigger(agent, f"评审这段代码:\n\n{CODE}", source="api")
    tracing = result.extra.get("tracing", {})
    print(f"\n=== [{backend}] {agent} (耗时 {tracing.get('duration_ms', '?')}ms) ===")
    print(result.output)


async def main() -> None:
    await agent_gateway.startup()
    try:
        for backend, agent in PAIR:
            try:
                await run_one(backend, agent)
            except Exception as e:
                print(f"\n=== [{backend}] {agent} 失败: {e}")
    finally:
        await agent_gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
