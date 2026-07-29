"""AI Agent 框架核心抽象。

定义所有后端(deepagents/agentscope)和所有拓扑(single/subagent/...)共享的基础类型:
- AgentRunContext: 一次运行的上下文(agent 名、消息、会话、调用树定位、可变状态)
- AgentResult: 运行结果(输出文本 + 元信息)

这些是「框架内部数据结构」,不绑定任何具体后端库。
后端适配器(runners/backends)负责把 AgentRunContext 翻译成各自库的输入,
把各自库的输出翻译回 AgentResult。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logging_config import get_logger

# 消息用最通用的 dict 表示:{"role": "system"|"user"|"assistant", "content": str}
Message = dict[str, str]

# 触发来源:记录运行是哪个入口发起的,写入 agent_runs.trigger_source
TriggerSource = Literal["api", "scheduler", "internal"]


@dataclass
class AgentRunContext:
    """一次 agent 运行的上下文(可变,中间件会修改 messages)。

    字段:
    - agent_name:  agent 标识(agents 目录名)
    - messages:    当前要喂给后端的消息列表(中间件可增删改)
    - session_id:  会话 id(持续对话模式;trigger 模式为 None)
    - source:      触发来源(api/scheduler/internal)
    - parent_run_id / depth: 调用树定位(多拓扑下成员 agent 调用时填父 run_id)
    - user_id:     当前用户(可选,用于持久记忆按用户隔离)
    - logger:      该次运行专属 logger(自动带 request_id)
    - extra:       自由扩展位(中间件可挂自己的中间状态,如 tracing 的步数)
    """

    agent_name: str
    messages: list[Message]
    session_id: str | None = None
    source: TriggerSource = "api"
    parent_run_id: str | None = None
    depth: int = 0
    user_id: str | None = None
    logger: logging.Logger = field(default_factory=lambda: get_logger("app.ai"))
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def last_user_message(self) -> str:
        """最后一条 user 消息的文本(用于日志/记录)。"""
        for m in reversed(self.messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""


@dataclass
class AgentResult:
    """一次 agent 运行的结果。

    output:  最终输出文本
    tokens:  token 用量(若后端能拿到)
    extra:   后端特定的附加信息(如 deepagents 的中间步骤、agentscope 的 usage)
    """

    output: str = ""
    tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 后端统一接口:所有拓扑运行器最终都要把消息交给一个「能跑的后端」。
# 这里定义后端的最小契约;具体由 backends/ 下的适配器实现。
# --------------------------------------------------------------------------
class BaseBackend:
    """后端最小契约:把消息跑成结果(同步/流式)。

    为什么不直接绑死 LangChain 或 agentscope 的返回类型:
    两个库的返回对象完全不同(deepagents 的 state dict / agentscope 的 Msg),
    这里统一成框架自己的 AgentResult,上层(runners/gateway)就不依赖具体库了。
    """

    backend_name: str = "base"

    async def invoke(self, ctx: AgentRunContext) -> AgentResult:
        raise NotImplementedError

    async def stream(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        raise NotImplementedError
        # 下面的 yield 让类型检查器识别为 async generator(永不执行)
        yield ""  # pragma: no cover


__all__ = [
    "Message",
    "TriggerSource",
    "AgentRunContext",
    "AgentResult",
    "BaseBackend",
]
