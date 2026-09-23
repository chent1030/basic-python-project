"""Coverage 测试 mock：FakeCoverageClient 模拟 Java 四端点。

Fixtures（coverage_engine / coverage_session_factory）已迁移到 tests/skill/conftest.py，
本模块仅保留 FakeCoverageClient 与分析端点签名，便于 application 测试用例注入。
"""
from __future__ import annotations

from typing import Any


class FakeCoverageClient:
    """模拟 Java 端 coverage 四端点（按 kind 分桶 + 分页）。"""

    def __init__(self, **kinds_data: list[dict]) -> None:
        self._kinds = dict(kinds_data)
        self.calls: list[dict[str, Any]] = []
        self.should_fail: bool = False
        self.delay_seconds: float = 0.0

    async def fetch_frequency(
        self, start, end, *, page=1, size=100,
        factory=None, area=None, category_l1_id=None, **kwargs,
    ):
        return await self._fetch(
            "frequency", start, end, page, size,
            {"factory": factory, "area": area, "category_l1_id": category_l1_id, **kwargs},
        )

    async def fetch_region_supervisor(
        self, start, end, *, page=1, size=100, region=None, **kwargs,
    ):
        return await self._fetch(
            "region_supervisor", start, end, page, size, {"region": region, **kwargs},
        )

    async def fetch_recurrence(
        self, start, end, *, page=1, size=100, threshold=2, **kwargs,
    ):
        return await self._fetch(
            "recurrence", start, end, page, size, {"threshold": threshold, **kwargs},
        )

    async def fetch_gaps(
        self, start, end, *, page=1, size=100,
        factory=None, area=None, category_l1_id=None, **kwargs,
    ):
        return await self._fetch(
            "gaps", start, end, page, size,
            {"factory": factory, "area": area, "category_l1_id": category_l1_id, **kwargs},
        )

    async def _fetch(
        self, kind: str, start: str, end: str, page: int, size: int, kwargs: dict,
    ):
        import asyncio
        self.calls.append(
            {"kind": kind, "start": start, "end": end, "page": page, "size": size, **kwargs},
        )
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            from app.skill.coverage.infrastructure.java_client import (
                JavaCoverageFetchError,
            )
            raise JavaCoverageFetchError(f"simulated failure for {kind}")
        data = self._kinds.get(kind) or []
        start_idx = (page - 1) * size
        end_idx = start_idx + size
        items = data[start_idx:end_idx]
        return {"items": items, "total_pages": 1, "page": page, "size": size}


__all__ = ["FakeCoverageClient"]