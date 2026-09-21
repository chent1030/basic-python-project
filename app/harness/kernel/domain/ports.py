from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol


class Repository(Protocol):
    def tenants(self) -> list[str]: ...

    def transaction(self) -> AbstractContextManager: ...

    def get(self, tenant: str, kind: str, key: str) -> dict[str, Any] | None: ...

    def put(self, tenant: str, kind: str, key: str, value: dict[str, Any]) -> None: ...

    def scan(self, tenant: str, kind: str) -> Iterator[dict[str, Any]]: ...

    def event(self, tenant: str, run: str, kind: str, data: dict[str, Any]) -> int: ...

    def events(
        self, tenant: str, run: str, after: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...


class Engine(Protocol):
    async def invoke(
        self, definition: Any, inputs: Any, context: Any, resume: Any = None
    ) -> Any: ...
