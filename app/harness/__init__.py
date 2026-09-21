"""DDD Agent kernel namespace with lazy, legacy document-review exports.

New workflows must import from app.harness.kernel. The exports here only
support existing document-review code; importing the kernel does not load them.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.harness.agents.single import BaseSingleAgent as BaseSingleAgent
    from app.harness.base import BaseAgent as BaseAgent
    from app.harness.communication import Blackboard as Blackboard
    from app.harness.communication import EventBus as EventBus
    from app.harness.communication import MessageBus as MessageBus
    from app.harness.context import AgentResult as AgentResult
    from app.harness.context import AgentRunContext as AgentRunContext
    from app.harness.context import Message as Message
    from app.harness.tools import tool as tool

_LEGACY_EXPORTS = {
    "BaseAgent": "app.harness.base",
    "BaseSingleAgent": "app.harness.agents.single",
    "AgentRunContext": "app.harness.context",
    "AgentResult": "app.harness.context",
    "Message": "app.harness.context",
    "tool": "app.harness.tools",
    "Blackboard": "app.harness.communication",
    "MessageBus": "app.harness.communication",
    "EventBus": "app.harness.communication",
}

__all__ = list(_LEGACY_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _LEGACY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
