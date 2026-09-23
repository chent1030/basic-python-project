"""Procedural API router 集成测试（FR-11）。

覆盖：retrieve / record / consolidate 三个端点 + 403 / 401 / 422。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.skill.procedural_memory.api.router import router as procedural_router
from app.skill.procedural_memory.application.services import ProceduralMemoryService

# ---------------------------------------------------------------- identity --


class _PrincipalStub:
    roles: list[str] = ["cps_admin"]

    def require(self, *roles: str) -> None:
        if not set(roles) & set(self.roles):
            from fastapi import HTTPException
            raise HTTPException(403, "Insufficient framework role")


class _ForbiddenPrincipal:
    roles: list[str] = ["viewer"]

    def require(self, *roles: str) -> None:
        from fastapi import HTTPException
        raise HTTPException(403, "Insufficient framework role")


def _override_identity():
    return _PrincipalStub()


def _override_forbidden():
    return _ForbiddenPrincipal()


class FakeProceduralClient:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []
        self.calls: list[dict] = []

    async def iter_all_pages(self, start, end, *, size=100, **kwargs):
        self.calls.append({"start": start, "end": end, **kwargs})
        yield {"items": self._items, "total_pages": 1, "page": 1, "size": size}


# ---------------------------------------------------------------- fixtures --


@pytest.fixture
def app_with_repo(procedural_session_factory):
    """带 repository 的 app（用于 record / consolidate）。"""
    from app.skill.procedural_memory.infrastructure.repository import (
        ProceduralRepository,
    )

    app = FastAPI()
    app.include_router(procedural_router)
    repo = ProceduralRepository(procedural_session_factory)
    svc = ProceduralMemoryService(client=FakeProceduralClient([]), repository=repo)
    app.state.procedural_service = svc
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app


@pytest.fixture
def client_with_repo(app_with_repo):
    return TestClient(app_with_repo)


# ---------------------------------------------------------------- retrieve --


def test_retrieve_endpoint_returns_hints(client_with_repo):
    # 通过 dependency override 注入新的 client（含 items）
    new_client = FakeProceduralClient([
        {"category_l1_id": 1, "area": "B", "decision": "ACCEPT",
         "ai_relation": "AGREE", "factory": "A", "sample_count": 1},
    ])
    client_with_repo.app.state.procedural_service._client = new_client
    resp = client_with_repo.post(
        "/procedural/retrieve",
        json={"categoryL1Id": 1, "area": "B", "decision": "ACCEPT", "topK": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "hints" in body
    assert len(body["hints"]) == 1
    assert body["hints"][0]["pattern"]["decision"] == "ACCEPT"


# ---------------------------------------------------------------- record --


def test_record_endpoint_round_trips(client_with_repo):
    resp = client_with_repo.post(
        "/procedural/record",
        json={
            "categoryL1Id": 1, "area": "B", "decision": "ACCEPT",
            "aiRelation": "AGREE", "sampleReasons": ["r1"],
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pattern_id" in body
    assert body["sample_count"] == 1


def test_record_endpoint_accumulates(client_with_repo):
    payload = {
        "categoryL1Id": 1, "area": "B", "decision": "ACCEPT",
        "aiRelation": "AGREE", "sampleReasons": ["r1"], "sampleCount": 1,
    }
    client_with_repo.post("/procedural/record", json=payload)
    payload["sampleCount"] = 2
    payload["sampleReasons"] = ["r2"]
    resp = client_with_repo.post("/procedural/record", json=payload)
    assert resp.status_code == 202
    assert resp.json()["sample_count"] == 3


# ---------------------------------------------------------------- consolidate --


def test_consolidate_endpoint_returns_summary(client_with_repo):
    resp = client_with_repo.post(
        "/procedural/consolidate", json={"sinceDays": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "merged_count" in body
    assert "consolidated_groups" in body
    assert body["since_days"] == 30


def test_consolidate_endpoint_bad_since_days_returns_422(client_with_repo):
    resp = client_with_repo.post(
        "/procedural/consolidate", json={"sinceDays": 0},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- auth --


def test_retrieve_forbidden_when_wrong_role():
    app = FastAPI()
    app.include_router(procedural_router)
    app.state.procedural_service = ProceduralMemoryService(
        client=FakeProceduralClient([]), repository=None,
    )
    app.dependency_overrides[agent_runs.principal] = _override_forbidden
    raw = TestClient(app)
    resp = raw.post(
        "/procedural/retrieve", json={"categoryL1Id": 1},
    )
    assert resp.status_code == 403


def test_retrieve_unauthenticated_returns_401():
    raw_app = FastAPI()
    raw_app.include_router(procedural_router)
    raw_app.state.procedural_service = ProceduralMemoryService(
        client=FakeProceduralClient([]), repository=None,
    )
    raw = TestClient(raw_app)
    resp = raw.post("/procedural/retrieve", json={"categoryL1Id": 1})
    assert resp.status_code == 401
