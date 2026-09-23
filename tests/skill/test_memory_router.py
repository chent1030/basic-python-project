"""Memory API router 集成测试（FastAPI TestClient）。

仅覆盖：路由注册 / 请求体校验 / Identity 鉴权短路（401/403/422）/ 业务 happy path 走 SQLite。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.skill.memory.api.router import router as memory_router
from app.skill.memory.application.ingestion_service import MemoryIngestionService
from app.skill.memory.application.retrieval_service import MemoryRetrievalService
from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import EMBEDDING_DIM, MemoryEntry
from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding
from app.skill.memory.infrastructure.repository import MemoryRepository


# ---------------------------------------------------------------- identity --


class _PrincipalStub:
    roles: list[str] = ["cps_admin"]

    def require(self, *roles: str) -> None:
        if not set(roles) & set(self.roles):
            from fastapi import HTTPException

            raise HTTPException(403, "Insufficient framework role")


def _override_identity():
    return _PrincipalStub()


# ---------------------------------------------------------------- fixtures --


@pytest.fixture
def app(memory_session_factory):
    """最小 FastAPI app + 注入 (memory_repository, ingestion, retrieval, java_client=None)。"""
    app = FastAPI()
    app.include_router(memory_router)

    repo = MemoryRepository(memory_session_factory)
    embed = HashPlaceholderEmbedding()
    ingestor = MemoryIngestionService(repo, embed)
    retrieval = MemoryRetrievalService(repo, embed)
    app.state.memory_services = (repo, ingestor, retrieval, None)
    # Identity stub：覆盖 agent_runs.principal（Annotated 别名不可直接 override）
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def repository(memory_session_factory):
    return MemoryRepository(memory_session_factory)


@pytest.fixture
def ingest_payload() -> dict:
    return {
        "id": 9001,
        "issue_id": 200,
        "version_no": 1,
        "decision": "APPROVE",
        "ai_relation": "MATCH",
        "reason": "AI 误判，已修正",
        "reviewer": "tester",
        "factory": "A",
        "area": "B",
        "category_l1_id": 1,
        "severity": 3,
        "tags": ["weekly-review"],
    }


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_post_ingest_adjudication_happy_path(client: TestClient, ingest_payload: dict):
    resp = client.post("/memory/ingest/adjudication", json=ingest_payload)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["source_table"] == "ADJUDICATION"
    assert body["source_id"] == 9001


@pytest.mark.asyncio
async def test_post_ingest_event_happy_path(client: TestClient):
    resp = client.post(
        "/memory/ingest/event",
        json={"id": 7001, "issue_id": 200, "kind": "AI_AUTO_CONFIRMED", "detail": "ok"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["source_table"] == "EVENT"


@pytest.mark.asyncio
async def test_post_ingest_adjudication_validation_422(client: TestClient):
    """缺 source_id 之类必填字段 → 422。"""
    resp = client.post("/memory/ingest/adjudication", json={"foo": "bar"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_retrieve_with_issue(client: TestClient, repository: MemoryRepository):
    await repository.upsert_judgment(MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=5001,
        issue_id=200,
        factory="A",
        area="B",
        embedding=[0.1] * EMBEDDING_DIM,
        payload={"decision": "APPROVE"},
    ))
    issue = {"factory": "A", "area": "B"}
    resp = client.get(
        "/memory/retrieve",
        params={"issueJson": json.dumps(issue), "topK": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # retrieve 端点返回 {hints: [...], prompt_payload: ...}
    assert "hints" in body
    assert isinstance(body["hints"], list)
    assert len(body["hints"]) >= 1
    assert "score" in body["hints"][0]
    assert "prompt_payload" in body


@pytest.mark.asyncio
async def test_get_patterns_empty(client: TestClient):
    resp = client.get("/memory/patterns", params={"scenario": "rust", "topK": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["patterns"] == []


@pytest.mark.asyncio
async def test_post_backfill_without_java_client_reports_error(client: TestClient):
    """未注入 java_client 时回填走 soft-fail 路径，错误被记入 summary.errors。"""
    resp = client.post(
        "/memory/backfill",
        json={"startIso": "2024-01-01", "endIso": "2024-12-31"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # 期望：3 个 kind 都因 java client 未配置而失败，但回填接口不抛 5xx
    assert body["adjudications"] == 0
    assert body["events"] == 0
    assert body["issues"] == 0
    assert any("java client" in e for e in body["errors"])


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401():
    """不通过 dependency_overrides → 走真实 principal → testclient.host 非 trusted → 401。"""
    raw_app = FastAPI()
    raw_app.include_router(memory_router)
    # 注：state 没注入，401 应当先于 503
    raw_client = TestClient(raw_app)
    resp = raw_client.post(
        "/memory/ingest/adjudication",
        json={"id": 1, "decision": "APPROVE"},
    )
    assert resp.status_code == 401


__all__ = []