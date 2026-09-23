"""Effect API router 集成测试（FR-12）。

覆盖：snapshot / compare / regression 三端点 + 422 / 401 / 403。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.skill.effect_evaluation.api.router import router as effect_router
from app.skill.effect_evaluation.application.services import EffectEvaluationService
from app.skill.effect_evaluation.infrastructure.repository import EffectMetricRepository

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


class FakeEffectClient:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []

    async def iter_all_pages(self, start, end, *, size=100, **kwargs):
        yield {"items": self._items, "total_pages": 1, "page": 1, "size": size}


PS1 = "2024-01-01T00:00:00+00:00"
PE1 = "2024-01-31T00:00:00+00:00"
PS2 = "2024-02-01T00:00:00+00:00"
PE2 = "2024-02-29T00:00:00+00:00"


def _metric_dict(**over) -> dict:
    base = {
        "aiPassRate": 0.85, "humanOverrideRate": 0.12,
        "recurrenceRate30d": 0.05, "coverageGapCount": 7,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- fixtures --


@pytest.fixture
def app_with_repo(effect_session_factory):
    app = FastAPI()
    app.include_router(effect_router)
    repo = EffectMetricRepository(effect_session_factory)
    svc = EffectEvaluationService(
        client=FakeEffectClient([_metric_dict()]), repository=repo,
    )
    app.state.effect_service = svc
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app


@pytest.fixture
def client_with_repo(app_with_repo):
    return TestClient(app_with_repo)


# ---------------------------------------------------------------- snapshot --


def test_snapshot_endpoint_round_trips(client_with_repo):
    resp = client_with_repo.post(
        "/effect/snapshot", json={"periodStart": PS1, "periodEnd": PE1},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["period_start"] == PS1
    assert body["scope_key"] == "_"


def test_snapshot_endpoint_bad_iso_returns_422(client_with_repo):
    resp = client_with_repo.post(
        "/effect/snapshot", json={"periodStart": "not-iso", "periodEnd": PE1},
    )
    assert resp.status_code == 422


def test_snapshot_forbidden_when_wrong_role():
    app = FastAPI()
    app.include_router(effect_router)
    app.state.effect_service = EffectEvaluationService(
        client=FakeEffectClient([]), repository=None,
    )
    app.dependency_overrides[agent_runs.principal] = _override_forbidden
    raw = TestClient(app)
    resp = raw.post(
        "/effect/snapshot", json={"periodStart": PS1, "periodEnd": PE1},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------- compare --


def test_compare_endpoint_returns_diff_list(client_with_repo, effect_session_factory):
    repo = EffectMetricRepository(effect_session_factory)
    client_with_repo.app.state.effect_service._repository = repo
    import asyncio

    async def _seed():
        await repo.upsert_snapshot(
            PS1, PE1, "_",
            {"ai_pass_rate": 0.85, "human_override_rate": 0.10,
             "recurrence_rate_30d": 0.03, "coverage_gap_count": 5},
        )
        await repo.upsert_snapshot(
            PS2, PE2, "_",
            {"ai_pass_rate": 0.60, "human_override_rate": 0.25,
             "recurrence_rate_30d": 0.025, "coverage_gap_count": 8},
        )

    asyncio.get_event_loop().run_until_complete(_seed())

    resp = client_with_repo.get(
        "/effect/compare",
        params={
            "baselineStart": PS1, "baselineEnd": PE1,
            "currentStart": PS2, "currentEnd": PE2,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "comparisons" in body
    assert len(body["comparisons"]) >= 1


def test_compare_endpoint_missing_period_returns_422(client_with_repo):
    resp = client_with_repo.get(
        "/effect/compare", params={"baselineStart": "bogus"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- regression --


def test_regression_endpoint_returns_metric_keys(
    client_with_repo, effect_session_factory,
):
    repo = EffectMetricRepository(effect_session_factory)
    client_with_repo.app.state.effect_service._repository = repo
    import asyncio

    async def _seed():
        await repo.upsert_snapshot(
            "2023-11-01T00:00:00+00:00", "2023-11-30T00:00:00+00:00", "_",
            {"ai_pass_rate": 0.95, "human_override_rate": 0.05,
             "recurrence_rate_30d": 0.01, "coverage_gap_count": 2},
        )
        await repo.upsert_snapshot(
            PS1, PE1, "_",
            {"ai_pass_rate": 0.50, "human_override_rate": 0.20,
             "recurrence_rate_30d": 0.03, "coverage_gap_count": 4},
        )

    asyncio.get_event_loop().run_until_complete(_seed())

    resp = client_with_repo.get(
        "/effect/regression",
        params={"periodStart": PS1, "periodEnd": PE1, "baselineDays": 90},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "regression_metric_keys" in body
    assert "ai_pass_rate" in body["regression_metric_keys"]
