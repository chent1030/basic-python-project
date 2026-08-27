"""黑板 ContextVar —— 让普通 @tool 函数能访问当前 agent 的共享黑板。

deepagents / llm 后端调用工具时,工具是纯函数、拿不到 AgentRunContext/黑板。
这里用一个 ContextVar 黑板指针,在 _run_member 里 set() 到当前 agent 的共享黑板,
工具通过 blackboard_var.get() 即可读写,实现「指定数据获取传递」。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from app.harness.communication.blackboard import Blackboard

# 当前共享黑板指针(同一 Pipeline 内所有 agent 共享)。未设置时为 None。
blackboard_var: ContextVar[Blackboard | None] = ContextVar("harness_blackboard", default=None)


def get_blackboard() -> Blackboard:
    """取当前共享黑板。未注入时抛错,便于定位漏配。"""
    bb = blackboard_var.get()
    if bb is None:
        raise RuntimeError("黑板未注入:工具只能在 agent 运行上下文中访问黑板。")
    return bb


def bind_blackboard(bb: Blackboard) -> Token[Blackboard | None]:
    """绑定黑板并返回 Token，供调用方恢复上一层上下文。"""
    return blackboard_var.set(bb)


def reset_blackboard(token: Token[Blackboard | None]) -> None:
    """使用绑定时返回的 Token 恢复上一层黑板。"""
    blackboard_var.reset(token)


@contextmanager
def use_blackboard(bb: Blackboard) -> Iterator[Blackboard]:
    """在一个同步/异步调用区间内绑定黑板。"""
    token = bind_blackboard(bb)
    try:
        yield bb
    finally:
        reset_blackboard(token)


__all__ = [
    "bind_blackboard",
    "blackboard_var",
    "get_blackboard",
    "reset_blackboard",
    "use_blackboard",
]
