"""Harness 核心数据结构:AgentRunContext / AgentResult / Message。

AgentRunContext 贯穿一次 agent 运行,含消息 + 通讯设施(黑板/消息/事件)。
AgentResult 是统一的输出格式。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.harness.communication import Blackboard, EventBus, MessageBus

# 消息用最通用的 dict 表示:{"role": "system"|"user"|"assistant", "content": str}
Message = dict[str, str]


@dataclass
class AgentResult:
    """agent 运行结果的统一格式。"""

    output: str = ""
    tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunContext:
    """一次 agent 运行的上下文。含消息 + 通讯设施。

    通讯设施由 Pipeline/Gateway 在 run() 时创建,注入到 Context,
    同一 Pipeline 内所有 agent 共享(黑板/消息/事件)。
    """

    agent_name: str = ""
    messages: list[Message] = field(default_factory=list)
    session_id: str | None = None
    source: str = "api"                  # api | scheduler | internal
    parent_run_id: str | None = None
    depth: int = 0
    user_id: str | None = None
    run_id: str = ""

    # 通讯设施(框架注入,所有 agent 共享)
    blackboard: Blackboard = field(default_factory=Blackboard)
    message_bus: MessageBus = field(default_factory=MessageBus)
    event_bus: EventBus = field(default_factory=EventBus)

    # 元信息
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("app.harness"))
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = uuid.uuid4().hex

    @property
    def last_user_message(self) -> str:
        """最后一条 user 消息的文本。"""
        for m in reversed(self.messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""


__all__ = ["AgentRunContext", "AgentResult", "Message"]
