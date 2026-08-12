"""Harness 框架 —— 通用 agent 基类体系 + agent 间通讯。

核心:
- 拓扑=基类(继承式):BaseSingleAgent/BaseParallelAgent/BasePipelineAgent/...
- 配置=类属性(纯 Python,无 config.yml)
- agent 间通讯:共享黑板 + 消息传递 + 事件总线
- 3 个后端可切换:deepagents / agentscope / llm
- 7 个中间件(洋葱模型) + HITL + 记忆 + 工具 + 聚合器

业务用法:
    from app.harness import BaseSingleAgent, BasePipelineAgent, PipelineStep, tool

    class TypoChecker(BaseSingleAgent):
        name = "typo"
        backend = "deepagents"
        system_prompt = "你是文字校对专家"
        middleware = ["tracing", "filter"]

    agent = TypoChecker()
    result = await agent.run("检查这段文字的错别字")
"""
from __future__ import annotations

# 拓扑基类
from app.harness.agents import (
    BaseConversationalAgent,
    BaseParallelAgent,
    BasePipelineAgent,
    BasePlanExecuteAgent,
    BaseReflectionAgent,
    BaseRouterAgent,
    BaseSequentialAgent,
    BaseSingleAgent,
    BaseSubagentAgent,
    PipelineStep,
)

# 工具 & 聚合器
from app.harness.aggregators import aggregator

# 基类
from app.harness.base import BaseAgent

# 通讯
from app.harness.communication import Blackboard, EventBus, MessageBus

# 数据结构
from app.harness.context import AgentResult, AgentRunContext, Message
from app.harness.tools import tool

__all__ = [
    # 底层基类
    "BaseAgent",
    # 拓扑基类
    "BaseSingleAgent",
    "BaseParallelAgent",
    "BaseSequentialAgent",
    "BasePipelineAgent",
    "PipelineStep",
    "BaseConversationalAgent",
    "BaseRouterAgent",
    "BasePlanExecuteAgent",
    "BaseReflectionAgent",
    "BaseSubagentAgent",
    # 数据结构
    "AgentRunContext",
    "AgentResult",
    "Message",
    # 工具 & 聚合器
    "tool",
    "aggregator",
    # 通讯
    "Blackboard",
    "MessageBus",
    "EventBus",
]
