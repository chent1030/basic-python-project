"""Coverage API router 集成测试（FastAPI TestClient）。

覆盖：GET analyze round-trip / 422 / 四 kinds 排列 / POST snapshot round-trip / 503 / 401。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.skill.coverage.api.router import router as coverage_router
from app.skill.coverage.application.services import (
    CoverageAnalysisService,
    CoverageIngestionService,
)
from app.skill.coverage.infrastructure.repository import CoverageRepository
from tests.skill.coverage_helpers import FakeCoverageClient

# ---------------------------------------------------------------- identity --


class _PrincipalStub:
    roles: list[str] = ["cps_admin"]

    def require(self, *roles: str) -> None:
        if not set(roles) & set(self.roles):
            from fastapi import HTTPException

            raise HTTPException(403, "Insufficient framework role")


def _override_identity():
    return _PrincipalStub()


class _ForbiddenPrincipal:
    roles: list[str] = ["viewer"]

    def require(self, *roles: str) -> None:
        from fastapi import HTTPException

        raise HTTPException(403, "Insufficient framework role")


def _override_forbidden():
    return _ForbiddenPrincipal()


def _d(**kw):
    """短写：dict 字段顺序自由，避免长行 E501。"""
    return kw


PS = "2024-01-01T00:00:00+00:00"
PE = "2024-01-31T00:00:00+00:00"


# ---------------------------------------------------------------- fixtures --


@pytest.fixture
def app(coverage_engine, coverage_session_factory):
    """FastAPI app + 注入 (analysis, ingestion) 服务。"""
    app = FastAPI()
    app.include_router(coverage_router)

    repo = CoverageRepository(coverage_session_factory)
    client = FakeCoverageClient(
        frequency=[
            _d(factory="A", area="B", category_l1_id=1,
               issue_count=10, closed_count=5,
               overdue_count=2, recurrence_count=1),
            _d(factory="A", area="B", category_l1_id=2,
               issue_count=4, closed_count=4,
               overdue_count=0, recurrence_count=0),
        ],
        region_supervisor=[
            _d(region="R1", supervisor_emp_no="S1",
               open_count=5, overdue_count=5, handled_count=0),
            _d(region="R1", supervisor_emp_no="S2",
               open_count=0, overdue_count=1, handled_count=5),
        ],
        recurrence=[
            _d(issue_id=1, recurrence_count=4,
               last_recurrence_at="2024-01-10",
               factory="A", area="B", category_l1_id=1),
        ],
        gaps=[
            _d(storage_room_type="cold", gap_severity=3,
               days_since_last_record=10,
               factory="A", area="B", category_l1_id=1),
        ],
    )
    analysis = CoverageAnalysisService(client, repo)
    ingestion = CoverageIngestionService(repo)
    app.state.coverage_services = (analysis, ingestion)
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app


@pytest.fixture
def app_no_repo():
    """无 repository 的 app（纯计算模式）—— 供 GET analyze + POST snapshot 503 测试。"""
    app = FastAPI()
    app.include_router(coverage_router)
    client = FakeCoverageClient(
        frequency=[
            _d(factory="A", area="B", category_l1_id=1,
               issue_count=5, closed_count=3,
               overdue_count=0, recurrence_count=0),
        ],
    )
    analysis = CoverageAnalysisService(client, repository=None)
    app.state.coverage_services = (analysis, None)
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def client_no_repo(app_no_repo):
    return TestClient(app_no_repo)


# ---------------------------------------------------------------- tests --


def test_get_analyze_round_trip_runs_all_kinds(client: TestClient):
    resp = client.get(
        "/coverage/analyze",
        params={"periodStart": PS, "periodEnd": PE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "frequency_trends" in body
    assert "region_supervisor_loads" in body
    assert "recurrences" in body
    assert "gaps" in body
    assert "summary_metrics" in body
    assert "anomalies" in body
    # 全部分析 → summary_metrics 总条目 = 10+4 = 14
    assert body["summary_metrics"]["total_issues"] == 14


def test_get_analyze_with_kinds_subset(client: TestClient):
    resp = client.get(
        "/coverage/analyze",
        params={
            "periodStart": PS, "periodEnd": PE, "kinds": "frequency,gaps",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["frequency_trends"]
    assert body["gaps"]
    assert body["region_supervisor_loads"] == []
    assert body["recurrences"] == []


def test_get_analyze_bad_period_returns_422(client: TestClient):
    resp = client.get(
        "/coverage/analyze",
        params={"periodStart": "not-iso", "periodEnd": "2024-01-31"},
    )
    assert resp.status_code == 422


def test_get_analyze_unknown_kinds_returns_422(client: TestClient):
    resp = client.get(
        "/coverage/analyze",
        params={
            "periodStart": PS, "periodEnd": PE, "kinds": "bogus",
        },
    )
    assert resp.status_code == 422


def test_get_analyze_forbidden_when_wrong_role():
    app = FastAPI()
    app.include_router(coverage_router)
    client = FakeCoverageClient(frequency=[])
    analysis = CoverageAnalysisService(client, repository=None)
    app.state.coverage_services = (analysis, None)
    app.dependency_overrides[agent_runs.principal] = _override_forbidden
    raw = TestClient(app)
    resp = raw.get(
        "/coverage/analyze",
        params={"periodStart": PS, "periodEnd": PE},
    )
    assert resp.status_code == 403


def test_get_analyze_unauthenticated_returns_401():
    """不走 dependency_overrides → 走真实 principal → 401。"""
    raw_app = FastAPI()
    raw_app.include_router(coverage_router)
    raw_client = TestClient(raw_app)
    resp = raw_client.get(
        "/coverage/analyze",
        params={"periodStart": PS, "periodEnd": PE},
    )
    assert resp.status_code == 401


def test_post_snapshot_round_trip(client: TestClient):
    body = {"periodStart": PS, "periodEnd": PE}
    resp = client.post("/coverage/snapshot", json=body)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert "snapshot_id" in out
    assert out["period_start"] == PS
    assert "summary_metrics" in out


def test_post_snapshot_without_repo_returns_503(client_no_repo: TestClient):
    body = {"periodStart": PS, "periodEnd": PE}
    resp = client_no_repo.post("/coverage/snapshot", json=body)
    assert resp.status_code == 503


def test_post_snapshot_missing_period_returns_422(client: TestClient):
    resp = client.post("/coverage/snapshot", json={"foo": "bar"})
    assert resp.status_code == 422


def test_post_snapshot_forbidden_when_wrong_role():
    app = FastAPI()
    app.include_router(coverage_router)
    client = FakeCoverageClient(frequency=[])
    analysis = CoverageAnalysisService(client, repository=None)
    app.state.coverage_services = (analysis, None)
    app.dependency_overrides[agent_runs.principal] = _override_forbidden
    raw = TestClient(app)
    resp = raw.post(
        "/coverage/snapshot",
        json={"periodStart": PS, "periodEnd": PE},
    )
    assert resp.status_code == 403