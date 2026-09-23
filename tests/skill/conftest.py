"""记忆体系测试基建（I 线）。

要点：
- SQLite 内存库 + Base.metadata.create_all（pgvector 在 sqlite 退化为 JSON 字符串）；
- EmbeddingClient 用 HashPlaceholderEmbedding（SHA256 → 1024-dim 归一化），确定性高；
- MemoryRepository 走 session_factory 注入；Java 客户端 FakeJavaClient 拉取模拟分页；
"""
from __future__ import annotations

from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # 注册全部 ORM 到 Base.metadata
from app.db.base import Base
from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding
from app.skill.memory.infrastructure.repository import MemoryRepository


@pytest_asyncio.fixture
async def sqlite_memory_engine():
    """独立 sqlite 引擎 + create_all memory 体系表。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_session_factory(sqlite_memory_engine):
    """Async session factory → MemoryRepository 用。"""
    factory = async_sessionmaker(sqlite_memory_engine, expire_on_commit=False)
    return factory


@pytest_asyncio.fixture
async def memory_repository(memory_session_factory) -> MemoryRepository:
    return MemoryRepository(memory_session_factory)


@pytest_asyncio.fixture
async def embedding_client() -> HashPlaceholderEmbedding:
    return HashPlaceholderEmbedding(dim=1024)


class FakeJavaClient:
    """模拟 Java callback / backfill 数据源（按 kind 分桶 + 分页）。"""

    def __init__(self, **kinds_data: list[dict]) -> None:
        self._kinds = dict(kinds_data)
        self.calls: list[tuple[str, str, str, int, int]] = []
        self.should_fail: bool = False
        self.delay_seconds: float = 0.0

    async def fetch_adjudications(self, start, end, page, size):
        return await self._fetch("adjudications", start, end, page, size)

    async def fetch_events(self, start, end, page, size):
        return await self._fetch("events", start, end, page, size)

    async def fetch_issues(self, start, end, page, size):
        return await self._fetch("issues", start, end, page, size)

    async def _fetch(self, kind: str, start: str, end: str, page: int, size: int):
        import asyncio
        self.calls.append((kind, start, end, page, size))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            from app.skill.memory.infrastructure.java_client import JavaMemoryFetchError
            raise JavaMemoryFetchError(f"simulated failure for {kind}")
        data = self._kinds.get(kind) or []
        start_idx = (page - 1) * size
        end_idx = start_idx + size
        items = data[start_idx:end_idx]
        return {"items": items, "total_pages": 1, "page": page, "size": size}

    async def iter_all_pages(self, fetch_one, start_iso, end_iso, *, size: int = 50):
        from app.skill.memory.infrastructure.java_client import JavaMemoryFetchError
        if self.should_fail:
            raise JavaMemoryFetchError(f"simulated failure for {fetch_one.__name__}")
        # 与真接口对齐：调用 fetch_one → 拿到 page_resp (含 items/total_pages) → yield
        page_resp = await fetch_one(start_iso, end_iso, page=1, size=size)
        yield page_resp


__all__ = [
    "memory_repository",
    "memory_session_factory",
    "embedding_client",
    "FakeJavaClient",
]