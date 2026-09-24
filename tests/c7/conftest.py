"""C7 系统化测试 + J 线联调验收（波次 13）共享 fixture。

要点：
- 复用 tests/initial_review/conftest 的 sqlite 内存库 + 时钟注入模式；
- 复用 tests/projects/conftest 的 FastAPI + principal override 模式；
- 复用 tests/skill/conftest 的 memory_* / coverage_* fixtures（本地复刻，避免
  pytest conftest 跨目录不可见问题）；
- FakeMemoryRetriever：替身实现 MemoryRetrievalService 协议（retrieve_context_for_issue
  + format_hints_for_prompt），记录调用次数/参数以校验「AI 初审→检索→拼 prompt」闭环；
- 不依赖真实 Java 仓；coverage 用 tests.skill.coverage_helpers.FakeCoverageClient 兜底。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # 注册全部 ORM 到 Base.metadata
from app.api.v1.endpoints.agent_runs import Principal, principal
from app.db.base import Base
from app.skill.memory.domain.models import EMBEDDING_DIM
from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding
from app.skill.memory.infrastructure.repository import MemoryRepository


def naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --------------------------------------------------------------- 复用 fixtures --


@pytest_asyncio.fixture
async def sqlite_memory_engine():
    """独立 sqlite 引擎 + create_all memory 体系表（与 tests/skill/conftest 等价）。"""
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
    return HashPlaceholderEmbedding(dim=EMBEDDING_DIM)


@pytest_asyncio.fixture
async def sqlite_coverage_engine():
    """独立 sqlite 引擎 + create_all coverage 体系表。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def coverage_session_factory(sqlite_coverage_engine):
    """Async session factory → CoverageRepository 用。"""
    factory = async_sessionmaker(sqlite_coverage_engine, expire_on_commit=False)
    return factory


# --------------------------------------------------------------- 替身 -------


class FakeMemoryRetriever:
    """MemoryRetrievalService 协议替身（不依赖 pgvector / embedding 模型）。

    暴露两个方法供 InitialReviewService._retrieve_historical_hints 调用：
    - ``retrieve_context_for_issue(issue_dto, *, top_k=5)`` → list[RetrievalHint-likes]
    - ``format_hints_for_prompt(hints)`` → JSON 字符串（prompt v3 直接拼）

    记录：调用次数、最后一次的 issue_dto/top_k、返回的 hint 数。
    """

    def __init__(self, hints_seq: list[list[dict[str, Any]]] | None = None) -> None:
        # 每次 retrieve 取下一批；空则返回当前批（始终返回同一批），便于闭环断言
        self._seq = list(hints_seq or [[]])
        self._idx = 0
        self.calls: list[dict[str, Any]] = []
        self.last_hints: list[dict[str, Any]] | None = None

    async def retrieve_context_for_issue(
        self, issue_dto: dict[str, Any], *, top_k: int = 5
    ) -> list[Any]:
        self.calls.append({"issue_dto": dict(issue_dto), "top_k": top_k})
        if self._idx < len(self._seq):
            batch = self._seq[self._idx]
            self._idx += 1
        else:
            batch = self._seq[-1] if self._seq else []
        # 模拟 RetrievalHint 形态：带 score + to_prompt_dict
        out = []
        for raw in batch:
            raw = dict(raw)
            out.append(_HintProxy(raw))
        self.last_hints = batch
        return out[:top_k]

    def format_hints_for_prompt(
        self, hints: list[Any] | None
    ) -> str | None:
        if not hints:
            return None
        payload = []
        for h in hints:
            if hasattr(h, "to_prompt_dict"):
                payload.append(h.to_prompt_dict())
            elif isinstance(h, dict):
                payload.append(h)
            else:
                payload.append({"raw": str(h)})
        return json.dumps(payload, ensure_ascii=False)


class _HintProxy:
    """极简 RetrievalHint 替身（带 score + to_prompt_dict）。"""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.score = float(raw.get("score", 0.5))

    def to_prompt_dict(self, max_payload_chars: int = 240) -> dict[str, Any]:
        payload = dict(self._raw.get("payload", {}))
        return {
            "source_table": self._raw.get("source_table", "ADJUDICATION"),
            "source_id": self._raw.get("source_id", 0),
            "issue_id": self._raw.get("issue_id"),
            "score": round(self.score, 4),
            "payload": payload,
        }


# --------------------------------------------------------------- helpers ----


def build_initial_review_app(
    *,
    service: Any,
    roles: list[str] | None = None,
) -> FastAPI:
    """最小 FastAPI app（仅挂 agent_callbacks router + service 注入）。"""
    from app.api.v1.endpoints.agent_callbacks import router as callbacks_router

    app = FastAPI()
    app.include_router(callbacks_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin", "cps_supervisor"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600,
    )
    # service 已构建（测试自行管理 session_factory + callback_client）
    # 把 service 挂到 app.state，initial_review_service 依赖会优先读这里
    app.state.initial_review_service = service
    return app


__all__ = [
    "FakeMemoryRetriever",
    "build_initial_review_app",
    "naive_utc_now",
]
