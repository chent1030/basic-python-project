"""C-07 端点契约测试 — 通过 /api/v1/agent/inspection-plans/draft 投递。

复用 initial_review/test_router 的 principal override 模式：app.dependency_overrides
覆盖底层 principal 函数（Annotated 别名不可直接 override）。
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal
from app.api.v1.endpoints.inspection_plans import router as plans_router


def _build_app(roles: list[str] | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(plans_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600,
    )
    return app


def _post(app: FastAPI, payload: dict):
    return TestClient(app).post("/api/v1/agent/inspection-plans/draft", json=payload)


def test_endpoint_returns_draft_for_valid_payload() -> None:
    app = _build_app()
    resp = _post(app, {
        "source_run_id": "weekly-RECTIFY-20260920",
        "plan_type": "WEEKLY_RECTIFY",
        "title": "9月第3周整改复查",
        "factory": "F1",
        "area": "A1",
        "risk_basis": "周报指向重复发生",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft_id"].startswith("draft-")
    assert body["plan_type"] == "WEEKLY_RECTIFY"
    assert body["source_run_id"] == "weekly-RECTIFY-20260920"
    assert body["status"] == "DRAFT"
    assert body["model_version"].startswith("plan-draft/")
    tasks = body["draft_content_json"]["tasks"]
    assert len(tasks) == 3
    assert tasks[0]["task_type"] == "INSPECT_RECTIFY"


def test_endpoint_idempotency_replay_returns_same_draft_id() -> None:
    app = _build_app()
    payload = {
        "source_run_id": "weekly-PATROL-20260921",
        "plan_type": "WEEKLY_PATROL",
        "title": "9月第4周区域巡检",
    }
    first = _post(app, payload).json()
    second = _post(app, payload).json()
    assert first["draft_id"] == second["draft_id"]
    assert first["status"] == "DRAFT"
    assert second["status"] == "REPLAYED"


def test_endpoint_derives_idempotency_key_from_source_run_id() -> None:
    app = _build_app()
    payload = {
        "source_run_id": "weekly-CHECK-20260922",
        "plan_type": "WEEKLY_CHECK",
        "title": "9月第5周辅房点检",
    }
    a = _post(app, payload).json()
    b = _post(app, {**payload, "idempotency_key": "plan-draft-weekly-CHECK-20260922"}).json()
    assert a["draft_id"] == b["draft_id"]


def test_endpoint_422_missing_required_field() -> None:
    app = _build_app()
    resp = _post(app, {"plan_type": "WEEKLY_RECTIFY", "title": "t"})
    assert resp.status_code == 422


def test_endpoint_422_empty_string_rejected() -> None:
    app = _build_app()
    resp = _post(app, {
        "source_run_id": "  ",
        "plan_type": "WEEKLY_RECTIFY",
        "title": "t",
    })
    assert resp.status_code == 422


def test_endpoint_422_field_length_limit() -> None:
    app = _build_app()
    resp = _post(app, {
        "source_run_id": "r",
        "plan_type": "WEEKLY_RECTIFY",
        "title": "x" * 600,  # max 512
    })
    assert resp.status_code == 422


def test_endpoint_403_without_cps_admin_role() -> None:
    app = _build_app(roles=["cps_employee"])  # 缺 cps_admin
    resp = _post(app, {
        "source_run_id": "r",
        "plan_type": "WEEKLY_RECTIFY",
        "title": "t",
    })
    assert resp.status_code == 403