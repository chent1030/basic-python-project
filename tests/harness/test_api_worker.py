import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.agent_runs import install_error_handlers, router
from app.core.security import create_access_token, create_refresh_token
from app.harness.kernel import AgentDefinition, Approval, Runtime, SQLiteRepository, Step, Worker


@pytest.fixture
async def environment(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")
    runtime = Runtime(repository, workspace_root=tmp_path / "workspaces")
    runtime.register(
        "test",
        Step(
            "work",
            AgentDefinition("work", handler=lambda value, context: value),
            approval=Approval.before(),
        ),
    )
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(router)
    install_error_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield runtime, client
    repository.close()


def auth(tenant="tenant", roles=None, refresh=False):
    claims = {
        "sub": "engineer",
        "tenant_id": tenant,
        "roles": roles if roles is not None else ["run_reader", "run_operator", "approver"],
    }
    token = create_refresh_token(claims) if refresh else create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


async def test_api_auth_tenant_and_validation(environment):
    runtime, client = environment
    assert (await client.get("/agent-runs")).status_code == 401
    assert (await client.get("/agent-runs", headers=auth(refresh=True))).status_code == 403
    assert (await client.get("/agent-runs", headers=auth(roles=[]))).status_code == 403
    assert (
        await client.post(
            "/agent-runs",
            headers=auth(),
            json={
                "task_id": "task",
                "workflow": "missing",
                "inputs": {},
                "idempotency_key": "1",
            },
        )
    ).status_code == 422
    response = await client.post(
        "/agent-runs",
        headers=auth(),
        json={
            "task_id": "task",
            "workflow": "test",
            "inputs": {"value": 1},
            "idempotency_key": "1",
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert (await client.get(f"/agent-runs/{run_id}", headers=auth("other"))).status_code == 403
    assert (
        await client.get(f"/agent-runs/{run_id}/events", headers=auth("other"))
    ).status_code == 403


async def test_worker_approval_resume_and_event_replay(environment):
    runtime, client = environment
    response = await client.post(
        "/agent-runs",
        headers=auth(),
        json={
            "task_id": "task",
            "workflow": "test",
            "inputs": {"value": 1},
            "idempotency_key": "1",
        },
    )
    run_id = response.json()["id"]
    worker = Worker(runtime, interval=0.01)
    worker.start()
    try:
        async with asyncio.timeout(5):
            while runtime.get("tenant", run_id)["status"] != "waiting":
                await asyncio.sleep(0.01)
        approvals = (await client.get(f"/agent-runs/{run_id}/approvals", headers=auth())).json()
        response = await client.post(
            f"/agent-runs/{run_id}/approvals/{approvals[0]['id']}",
            headers=auth(),
            json={"edited": {"value": 2}},
        )
        assert response.status_code == 200, response.text
        async with asyncio.timeout(5):
            while runtime.get("tenant", run_id)["status"] != "succeeded":
                await asyncio.sleep(0.01)
        assert runtime.get("tenant", run_id)["output"] == {"value": 2}
        first = (await client.get(f"/agent-runs/{run_id}/events", headers=auth())).json()
        cursor = first[2]["cursor"]
        response = await client.get(
            f"/agent-runs/{run_id}/events?stream=true",
            headers={
                **auth(),
                "Last-Event-ID": str(cursor),
            },
        )
        assert response.status_code == 200
        assert f"id: {cursor}\n" not in response.text
        assert "run.succeeded" in response.text
        assert (
            await client.post(
                f"/agent-runs/{run_id}/approvals/{approvals[0]['id']}", headers=auth(), json={}
            )
        ).status_code == 409
    finally:
        await worker.stop()


async def test_application_lifespan_wires_and_closes_framework(tmp_path, monkeypatch):
    from app import main

    async def noop():
        return None

    for service in (main.datasources, main.llm, main.http_client, main.scheduler_service):
        monkeypatch.setattr(service, "startup", noop)
        monkeypatch.setattr(service, "shutdown", noop)
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "framework"))
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("AGENT_EMBEDDED_WORKER", "true")
    monkeypatch.setenv("AGENT_WORKFLOW_MODULES", "app.projects.agent_examples")
    app = main.create_app()
    async with main.lifespan(app):
        assert "document_analysis" in app.state.agent_runtime.workflows
        assert app.state.agent_worker.loop_task is not None
    assert app.state.agent_runtime is None
    assert app.state.agent_worker is None
