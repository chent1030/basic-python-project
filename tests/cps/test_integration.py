from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.api.v1.endpoints.agent_runs import install_error_handlers
from app.api.v1.endpoints.cps import router
from app.core.security import create_access_token
from app.harness.kernel import (
    DeepAgentsEngine,
    ModelConfiguration,
    Models,
    Runtime,
    SQLiteRepository,
)
from app.projects.cps.bootstrap import register
from app.projects.cps.domain.models import DispatchReview
from app.projects.cps.infrastructure.config import CPSConfig, EmbeddingsConfig
from app.projects.cps.infrastructure.evidence import HTTPImageEncoder

from .conftest import ScriptedEngine, create_started, execute_active, proposal
from .test_workflow import dispatch


def auth(actor="employee", tenant="tenant", roles=None):
    token = create_access_token(
        {
            "sub": actor,
            "tenant_id": tenant,
            "roles": roles or ["cps_employee"],
        }
    )
    return {"Authorization": f"Bearer {token}"}


async def test_api_auth_owner_tenant_schema_and_no_frontend(environment):
    service, runtime, _ = environment
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(router, prefix="/api/v1")
    install_error_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        endpoint = "/api/v1/cps/inspections"
        assert (await client.get(endpoint)).status_code == 401
        body = {
            "goal": "巡检",
            "line_info": {"line_id": "L1", "area": "A", "supervisor_id": "S"},
            "idempotency_key": "api-create",
        }
        response = await client.post(endpoint, json=body, headers=auth())
        assert response.status_code == 201, response.text
        case_id = response.json()["id"]
        assert (await client.post(endpoint, json=body, headers=auth())).json()["id"] == case_id
        assert (
            await client.get(f"{endpoint}/{case_id}", headers=auth(actor="other"))
        ).status_code == 403
        assert (
            await client.get(f"{endpoint}/{case_id}", headers=auth(tenant="other"))
        ).status_code == 403
        assert (await client.get(endpoint, headers=auth(actor="other"))).json() == []
        assert (
            await client.post(
                f"{endpoint}/{case_id}/dispatch",
                json={
                    "expected_version": 1,
                    "idempotency_key": "d",
                    "action": "approve",
                    "reason": "批准",
                },
                headers=auth(),
            )
        ).status_code == 403
        assert (
            await client.post(endpoint, json={**body, "owner": "admin"}, headers=auth())
        ).status_code == 422
        assert (await client.get("/api/v1/cps/statistics", headers=auth())).status_code == 403
        assert (
            await client.get("/api/v1/cps/statistics", headers=auth(roles=["cps_supervisor"]))
        ).status_code == 200
        assert (await client.get(f"{endpoint}/{case_id}/events", headers=auth())).status_code == 200
        assert (await client.get("/", headers=auth())).status_code == 404


async def test_restart_resumes_same_approved_main_without_second_model_call(tmp_path):
    path = tmp_path / "state.sqlite"
    records = SQLiteRepository(path)
    engine = ScriptedEngine()
    runtime = Runtime(records, engine, workspace_root=tmp_path / "workspaces")
    register(runtime, config=CPSConfig())
    service = runtime.cps_service
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    dispatch(service, case.id)
    records.close()
    records = SQLiteRepository(path)
    second_engine = ScriptedEngine()
    resumed = Runtime(records, second_engine, workspace_root=tmp_path / "workspaces")
    register(resumed, config=CPSConfig())
    try:
        service = resumed.cps_service
        run = await execute_active(service, resumed, case.id)
        service.advance("tenant", run["id"])
        assert run["status"] == "succeeded" and not second_engine.calls
        await execute_active(service, resumed, case.id)
        assert [name for name, _ in second_engine.calls] == ["issue_identification"]
        assert (
            len(
                [
                    job
                    for job in records.scan("tenant", "cps_job")
                    if job["agent"] == "issue_identification"
                ]
            )
            == 1
        )
    finally:
        records.close()


async def test_dispatch_review_outbox_recovers_crash_before_native_approval(
    environment, monkeypatch
):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    original = service.flush
    monkeypatch.setattr(
        service, "flush", lambda tenant: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    body = DispatchReview(
        expected_version=case.version, idempotency_key="durable", action="approve", reason="批准"
    )
    with pytest.raises(RuntimeError, match="crash"):
        service.dispatch("tenant", case.id, "admin", ["cps_admin"], body)
    monkeypatch.setattr(service, "flush", original)
    service.dispatch("tenant", case.id, "admin", ["cps_admin"], body)
    await execute_active(service, runtime, case.id)
    assert (
        len(
            [
                job
                for job in service.records.scan("tenant", "cps_job")
                if job["agent"] == "issue_identification"
            ]
        )
        == 1
    )


class StructuredModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_real_deepagents_graph_returns_structured_proposal_to_human_gate(tmp_path):
    proposal_output = proposal(None, "request_human")
    model = StructuredModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ProposalOutput",
                        "args": proposal_output,
                        "id": "structured1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    config = ModelConfiguration.model_validate(
        {
            "providers": {"test": {"base_url": "http://localhost:8001/v1"}},
            "models": {
                "default": {
                    "provider": "test",
                    "model": "offline",
                    "capabilities": ["vision", "tools", "structured_output"],
                },
            },
            "agents": {
                f"cps_{name}": "default"
                for name in (
                    "main",
                    "issue_identification",
                    "rectification_judgement",
                    "history_analysis",
                    "coverage_analysis",
                    "report",
                    "work_plan",
                    "observation",
                )
            },
        }
    )
    records = SQLiteRepository(tmp_path / "native.sqlite")
    engine = DeepAgentsEngine(Models(config), model_factory=lambda definition: model)
    runtime = Runtime(records, engine, workspace_root=tmp_path / "workspaces")
    register(runtime, config=CPSConfig())
    try:
        from app.projects.cps.domain.models import CreateInspection, VersionedCommand

        service = runtime.cps_service
        case = service.create(
            "tenant",
            "employee",
            CreateInspection(
                goal="需要人工明确范围",
                line_info={"line_id": "L1", "area": "A", "supervisor_id": "S"},
                idempotency_key="native",
            ),
        )
        service.start(
            "tenant",
            case.id,
            "employee",
            VersionedCommand(expected_version=1, idempotency_key="start"),
        )
        result = await execute_active(service, runtime, case.id)
        assert result["status"] == "waiting", result
        approval = service.detail("tenant", case.id)["approvals"][0]
        assert approval["payload"] == proposal_output
        assert any(
            event["kind"] == "engine.capabilities"
            for event in records.events("tenant", result["id"])
        )
    finally:
        records.close()


async def test_image_embedding_adapter_and_invalid_vectors(monkeypatch):
    original_client = httpx.AsyncClient
    requests = []
    response_vector = [1.0, 0.0, 0.5]

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"embedding": response_vector})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs,
            transport=httpx.MockTransport(respond),
        ),
    )
    encoder = HTTPImageEncoder(
        EmbeddingsConfig(url="https://embedding.test/images", model="vision-embed")
    )
    assert await encoder.encode("data:image/png;base64,AA==") == response_vector
    assert requests[0]["model"] == "vision-embed"
    response_vector = [0, 0, 0]
    with pytest.raises(ValueError, match="nonzero"):
        await encoder.encode("data:image/png;base64,AA==")
