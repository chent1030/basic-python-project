"""agent 间通讯(3 种模式)。

- Blackboard:共享黑板,所有 agent 读写同一空间
- MessageBus:消息传递,agent 间直接互发
- EventBus:事件总线,发布/订阅松耦合
"""
from __future__ import annotations

from app.harness.communication.blackboard import Blackboard, BlackboardEntry
from app.harness.communication.event_bus import Event, EventBus, EventHandler
from app.harness.communication.message_bus import Message, MessageBus

__all__ = [
    "Blackboard",
    "BlackboardEntry",
    "MessageBus",
    "Message",
    "EventBus",
    "Event",
    "EventHandler",
]
