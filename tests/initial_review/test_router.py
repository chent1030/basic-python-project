"""C-01/C-03 端点薄路由测试：202/幂等/409/404/鉴权。

服务层注入 sqlite 内存实现；identity 用 dependency_overrides 覆盖 principal。
全异步（httpx.ASGITransport，项目 pytest asyncio_mode=auto）。
"""

from __future__ import annotations

import time

import httpx

from app.api.v1.endpoints.agent_callbacks import initial_review_service
from app.api.v1.endpoints.agent_callbacks import router as agent_router
from app.api.v1.endpoints.agent_runs import Principal, principal
from tests.initial_review.conftest import (
    VALID_TEXT,
    make_request,
    make_service,
    make_session_factory,
)


def body(**over) -> dict:
    return make_request(**over).model_dump(mode="json")


async def make_client(roles: list[str] | None = None) -> httpx.AsyncClient:
    sf = await make_session_factory()
    service, _, _ = make_service(sf)
    app = _build_app(service, roles)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _build_app(service, roles: list[str] | None = None) -> object:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(agent_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600
    )
    app.dependency_overrides[initial_review_service] = lambda: service
    return app


async def test_c01_returns_202_with_task_ref() -> None:
    async with await make_client() as client:
        resp = await client.post("/api/v1/agent/rectifications", json=body())
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["review_task_ref"] == "cps-rectify-ISS-001-v1"
        assert data["status"] == "COMPLETED"
        assert data["replayed"] is False


async def test_c01_idempotent_replay_same_ref() -> None:
    async with await make_client() as client:
        first = await client.post("/api/v1/agent/rectifications", json=body())
        second = await client.post("/api/v1/agent/rectifications", json=body())
        assert first.status_code == second.status_code == 202
        assert first.json()["review_task_ref"] == second.json()["review_task_ref"]
        assert second.json()["replayed"] is True


async def test_c01_conflicting_replay_409() -> None:
    async with await make_client() as client:
        await client.post("/api/v1/agent/rectifications", json=body())
        resp = await client.post(
            "/api/v1/agent/rectifications", json=body(reason="不同参数必须冲突" + VALID_TEXT)
        )
        assert resp.status_code == 409


async def test_c03_status_found_and_404() -> None:
    async with await make_client() as client:
        await client.post("/api/v1/agent/rectifications", json=body())
        ok = await client.get("/api/v1/agent/rectifications/cps-rectify-ISS-001-v1")
        assert ok.status_code == 200
        assert ok.json()["task_id"] == "cps-rectify-ISS-001-v1"
        missing = await client.get("/api/v1/agent/rectifications/cps-rectify-NOPE-v1")
        assert missing.status_code == 404


async def test_validation_error_422() -> None:
    async with await make_client() as client:
        resp = await client.post("/api/v1/agent/rectifications", json={"issue_id": "X"})
        assert resp.status_code == 422


async def test_forbidden_without_cps_admin_role() -> None:
    async with await make_client(roles=["cps_employee"]) as client:
        resp = await client.post("/api/v1/agent/rectifications", json=body())
        assert resp.status_code == 403
