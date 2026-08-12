"""消息总线 —— agent 间直接互发消息。

agent A 直接给 agent B 发消息,B 处理后可回复。
精确控制发给谁,支持通知(send)和请求-响应(request)两种模式。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.harness.base import BaseAgent


@dataclass
class Message:
    """一条消息。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sender: str = ""               # 发送者 agent 名
    target: str = ""               # 接收者 agent 名
    content: str = ""              # 消息内容
    timestamp: float = field(default_factory=time.time)
    reply_to: str = ""             # 若是回复,原消息 id
    is_reply: bool = False
    reply_data: str = ""           # 回复内容(供 request() 取)


class MessageBus:
    """消息总线。同一 Pipeline 内所有 agent 共享。

    用法:
        await agent.send("writer", "建议修改:...")        # 通知
        reply = await agent.request("reviewer", "评审这段") # 等回复
        msgs = agent.receive_messages()                     # 取收件箱
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}        # name -> 实例
        self._mailbox: dict[str, list[Message]] = {}    # name -> 收件箱
        self._replies: dict[str, asyncio.Future] = {}   # msg_id -> Future(request 等回复用)

    def register(self, agent: BaseAgent) -> None:
        """注册 agent,使其可被 send/request 寻址。"""
        self._agents[agent.name] = agent
        self._mailbox.setdefault(agent.name, [])

    async def send(self, target: str, content: str, sender: str = "") -> None:
        """发消息到 target 的收件箱(通知模式,不等回复)。"""
        if target not in self._agents:
            return  # 目标不存在,静默丢弃(可加日志)
        msg = Message(sender=sender, target=target, content=content)
        self._mailbox.setdefault(target, []).append(msg)

    async def request(self, target: str, content: str, sender: str = "") -> str:
        """发消息并等待 target 的回复(请求-响应模式)。

        target 需调 reply(original_msg_id, reply_content) 回复。
        """
        msg = Message(sender=sender, target=target, content=content)
        self._mailbox.setdefault(target, []).append(msg)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._replies[msg.id] = future
        return await future

    async def reply(self, original_msg_id: str, content: str, sender: str = "") -> None:
        """回复一条 request 消息。"""
        future = self._replies.pop(original_msg_id, None)
        if future and not future.done():
            future.set_result(content)

    def receive(self, name: str) -> list[Message]:
        """取 name 收件箱的消息(取出后清空收件箱)。"""
        msgs = self._mailbox.get(name, [])
        self._mailbox[name] = []
        return msgs

    def peek(self, name: str) -> list[Message]:
        """查看收件箱但不清空(窥视)。"""
        return self._mailbox.get(name, [])

    def registered_agents(self) -> list[str]:
        return list(self._agents.keys())

    def clear(self) -> None:
        self._mailbox.clear()
        self._replies.clear()


__all__ = ["MessageBus", "Message"]
