from __future__ import annotations

from typing import Any, Protocol

from .models import Inspection


class InspectionRepository(Protocol):
    def get(self, tenant: str, inspection_id: str) -> Inspection: ...

    def save(self, tenant: str, case: Inspection) -> None: ...

    def list(self, tenant: str) -> list[Inspection]: ...

    def get_record(self, tenant: str, kind: str, key: str) -> dict[str, Any] | None: ...


class ImageEncoder(Protocol):
    async def encode(self, image: str) -> list[float]: ...


class ImageValidator(Protocol):
    def __call__(self, encoded: str, max_bytes: int) -> tuple[bytes, str, int, int]: ...
