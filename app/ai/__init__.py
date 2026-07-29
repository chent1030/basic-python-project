"""AI Agent 框架 —— 集成 deepagents + agentscope,支持 6 拓扑 + 4 类记忆。

模块导出:
- agent_gateway:统一入口(trigger / chat),挂进 app lifespan
- registry / config:agent 自动发现 + 单 agent 配置
- tool:@tool 装饰器,定义全局/专属工具

详见 README 的 AI Agent 框架章节。
"""
from __future__ import annotations

from app.ai.base import AgentResult, AgentRunContext
from app.ai.config import AgentConfig
from app.ai.gateway import agent_gateway
from app.ai.registry import registry
from app.ai.tools import tool

__all__ = [
    "agent_gateway",
    "registry",
    "AgentConfig",
    "AgentRunContext",
    "AgentResult",
    "tool",
]
