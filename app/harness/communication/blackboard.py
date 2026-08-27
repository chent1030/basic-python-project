"""共享黑板 —— 所有 agent 读写同一空间。

任一 agent 写入的数据,其它 agent 能读到。解耦:写入方不关心谁读,读取方不关心谁写。
类似协作画板/共享内存,适合多 agent 共享中间结果。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class BlackboardEntry:
    """黑板上的一个条目。"""

    value: Any
    writer: str = ""  # 写入者 agent 名
    timestamp: float = field(default_factory=time.time)


class Blackboard:
    """共享黑板。同一 Pipeline 内所有 agent 共享。

    用法:
        agent.write("research", result)      # 写
        data = agent.read("research")        # 读
        agent.has("research")                # 是否存在
    """

    def __init__(self) -> None:
        self._data: dict[str, BlackboardEntry] = {}
        self._history: dict[str, list[BlackboardEntry]] = {}
        self._lock = RLock()

    def write(self, key: str, value: Any, writer: str = "") -> None:
        """写入/更新一个 key。自动记录历史。"""
        entry = BlackboardEntry(value=value, writer=writer)
        with self._lock:
            self._data[key] = entry
            self._history.setdefault(key, []).append(entry)

    def extend_unique(
        self,
        key: str,
        values: Iterable[Any],
        *,
        identity: Callable[[Any], Any] | None = None,
        writer: str = "",
    ) -> list[Any]:
        """Atomically append values to a list entry while removing duplicates."""
        with self._lock:
            current = self._data.get(key)
            merged = list(current.value) if current and isinstance(current.value, list) else []
            identify = identity or (lambda item: item)
            seen: list[Any] = [identify(item) for item in merged]
            for value in values:
                marker = identify(value)
                if marker in seen:
                    continue
                merged.append(value)
                seen.append(marker)
            entry = BlackboardEntry(value=merged, writer=writer)
            self._data[key] = entry
            self._history.setdefault(key, []).append(entry)
            return list(merged)

    def read(self, key: str) -> Any | None:
        """读取一个 key 的值。不存在返回 None。"""
        with self._lock:
            entry = self._data.get(key)
            return entry.value if entry else None

    def has(self, key: str) -> bool:
        """key 是否存在。"""
        with self._lock:
            return key in self._data

    def keys(self) -> list[str]:
        """所有 key。"""
        with self._lock:
            return list(self._data.keys())

    def history(self, key: str) -> list[BlackboardEntry]:
        """某个 key 的写入历史(多次覆盖的记录)。"""
        with self._lock:
            return list(self._history.get(key, []))

    def snapshot(self) -> dict[str, Any]:
        """当前黑板所有值的快照(调试/记录用)。"""
        with self._lock:
            return {k: v.value for k, v in self._data.items()}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._history.clear()


__all__ = ["Blackboard", "BlackboardEntry"]
