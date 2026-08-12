"""事件总线 —— 发布/订阅模式,松耦合触发。

agent 发布事件,订阅者自动被调用。发布者不关心谁订阅。
适合「X 完成后自动触发 Y」的场景。
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

EventHandler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    """一个事件。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: str = ""                  # 事件类型(如 "ocr_done")
    data: Any = None                # 事件数据
    source: str = ""                # 发布者 agent 名
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """事件总线。同一 Pipeline 内所有 agent 共享。

    用法:
        # 订阅(通常在 agent 的 setup 里)
        agent.subscribe("ocr_done", self.on_ocr)

        # 发布(在 run 里)
        await agent.publish("ocr_done", result)

        # 事件处理器
        async def on_ocr(self, event):
            # OCR 完成后自动触发
            ...
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._event_log: list[Event] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """订阅事件。event_type 匹配时 handler 被调用。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """取消订阅。"""
        subs = self._subscribers.get(event_type, [])
        if handler in subs:
            subs.remove(handler)

    async def publish(self, event_type: str, data: Any = None, source: str = "") -> None:
        """发布事件。所有订阅者被异步调用(不阻塞发布者)。"""
        event = Event(type=event_type, data=data, source=source)
        self._event_log.append(event)
        for handler in self._subscribers.get(event_type, []):
            try:
                await handler(event)
            except Exception:
                import logging

                logging.getLogger("app.harness.event_bus").exception(
                    "事件 '%s' 处理器异常", event_type
                )

    def event_log(self) -> list[Event]:
        """已发布的所有事件(调试/审计用)。"""
        return list(self._event_log)

    def subscriber_count(self, event_type: str) -> int:
        return len(self._subscribers.get(event_type, []))

    def clear(self) -> None:
        self._subscribers.clear()
        self._event_log.clear()


__all__ = ["EventBus", "Event", "EventHandler"]
