"""Harness 框架 —— 通用 agent 基类体系 + agent 间通讯。

核心:
- 拓扑=基类(继承式):BaseSingleAgent/BaseParallelAgent/BasePipelineAgent/...
- 配置=类属性(纯 Python,无 config.yml)
- agent 间通讯:共享黑板 + 消息传递 + 事件总线
- 3 个后端可切换:deepagents / agentscope / llm
- 7 个中间件(洋葱模型) + HITL + 4 类记忆 + 工具 + 聚合器

业务用法:
    from app.harness import BaseSingleAgent

    class TypoChecker(BaseSingleAgent):
        name = "typo"
        backend = "deepagents"
        system_prompt = "你是文字校对专家"
        middleware = ["tracing", "filter"]

    agent = TypoChecker()
    result = await agent.run("检查这段文字的错别字")

注意:本文件随模块完成度逐步补充导出。以下为已完成的部分。
"""
from __future__ import annotations

# 已完成的底层
from app.harness.base import BaseAgent
from app.harness.context import AgentResult, AgentRunContext, Message

__all__ = [
    # 基类(待补充拓扑基类)
    "BaseAgent",
    # 数据结构
    "AgentRunContext",
    "AgentResult",
    "Message",
    # 拓扑基类/工具/聚合器/通讯 → 待后续模块完成后补充导出
]
