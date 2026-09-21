import asyncio
from dataclasses import replace

import httpx
import pytest

from app.harness.kernel import (
    AgentDefinition,
    Approval,
    DeploymentPolicy,
    ModelConfiguration,
    Models,
    Output,
    Parallel,
    Runtime,
    Sequence,
    SQLiteRepository,
    Step,
    attach_memory_observer,
)
from app.harness.kernel.domain.models import Forbidden
from app.harness.kernel.infrastructure.resources import ScopedCache


@pytest.fixture
def runtime(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")
    runtime = Runtime(repository, workspace_root=tmp_path / "workspaces", max_concurrency=1)
    yield runtime
    repository.close()


async def test_nested_request_does_not_deadlock_with_one_slot(runtime):
    child = AgentDefinition("child", handler=lambda value, context: value)

    async def invoke(value, context):
        return await context.agents.request("child", value, key="delegate")

    parent = AgentDefinition("parent", handler=invoke, delegates=("child",))
    runtime.register("child", Step("child", child))
    runtime.register("parent", Step("parent", parent))
    run = runtime.submit("tenant", "task", "parent", {"answer": 3}, idempotency_key="1")
    async with asyncio.timeout(3):
        result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"answer": 3}


async def test_shared_database_enforces_cross_runtime_capacity(runtime, tmp_path):
    second = Runtime(
        SQLiteRepository(tmp_path / "state.sqlite"),
        workspace_root=tmp_path / "workspaces",
        max_concurrency=1,
    )
    active = 0
    peak = 0

    async def handler(value, context):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "done"

    composition = Step("work", AgentDefinition("work", handler=handler))
    runtime.register("test", composition)
    second.register("test", composition)
    first_run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    second_run = second.submit("tenant", "task", "test", {}, idempotency_key="2")
    try:
        result = await asyncio.gather(
            runtime.execute("tenant", first_run["id"]), second.execute("tenant", second_run["id"])
        )
        assert all(run["status"] == "succeeded" for run in result), result
        assert peak == 1
    finally:
        second.repository.close()


async def test_mutation_cannot_change_other_branch_or_input_snapshot(runtime):
    original = {"items": [1]}

    def mutate(value, context):
        value["items"].append(2)
        return value

    runtime.register(
        "test",
        Parallel(
            Step("mutate", AgentDefinition("mutate", handler=mutate)),
            Step("read", AgentDefinition("read", handler=lambda value, context: value)),
        ),
    )
    run = runtime.submit("tenant", "task", "test", original, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert original == {"items": [1]}
    assert result["output"]["results"] == [{"items": [1, 2]}, {"items": [1]}]
    assert all(
        record["inputs"] == original for record in runtime.repository.scan("tenant", "invocation")
    )


async def test_inherited_policy_and_explicit_none(runtime):
    definition = AgentDefinition(
        "work", handler=lambda value, context: value, approval=Approval(mode="inherit")
    )
    runtime.register("test", Sequence(Step("work", definition), approval=Approval.before()))
    run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    assert (await runtime.execute("tenant", run["id"]))["status"] == "waiting"
    runtime.policy = DeploymentPolicy(require_approval=True)
    with pytest.raises(Forbidden):
        runtime.register("denied", Step("work", replace(definition, approval=Approval.none())))


def test_invalid_bindings_rejected_before_execution(runtime):
    definition = AgentDefinition("work", handler=lambda value, context: value)
    with pytest.raises(ValueError):
        runtime.register("bad", Step("work", definition, Output("future")))
    with pytest.raises(ValueError):
        runtime.register("bad", Parallel(Step("same", definition), Step("same", definition)))


def test_model_configuration_reference_capability_and_secret_validation():
    configuration = {
        "providers": {"local": {"base_url": "http://localhost:1234/v1"}},
        "models": {
            "small": {"provider": "local", "model": "small", "parameters": {"temperature": 0.1}}
        },
        "agents": {"worker": "small"},
    }
    models = Models(ModelConfiguration.model_validate(configuration))
    definition = AgentDefinition("worker", system_prompt="Bounded task")
    snapshot = models.snapshot(definition)
    assert snapshot["base_url"] == "http://localhost:1234/v1"
    assert snapshot["model"] == "small"
    configuration["models"]["small"]["parameters"]["default_headers"] = {"Authorization": "secret"}
    with pytest.raises(ValueError):
        ModelConfiguration.model_validate(configuration)


async def test_model_request_uses_configured_endpoint_and_model(monkeypatch):
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "completion",
                "object": "chat.completion",
                "created": 0,
                "model": "tiny",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    config = ModelConfiguration.model_validate(
        {
            "providers": {
                "local": {"base_url": "http://models.internal:8123/v1", "max_retries": 0}
            },
            "models": {
                "tiny": {
                    "provider": "local",
                    "model": "configured-tiny-model",
                    "parameters": {"temperature": 0.3, "max_tokens": 128},
                }
            },
            "agents": {"worker": "tiny"},
        }
    )
    model = Models(config).create(AgentDefinition("worker", system_prompt="Bounded task"))
    original = model.root_async_client._client
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.root_async_client._client = client
        result = await model.ainvoke("hello")
        assert result.content == "ok"
    await original.aclose()
    import json

    assert str(requests[0].url) == "http://models.internal:8123/v1/chat/completions"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "configured-tiny-model"
    assert payload["temperature"] == 0.3


async def test_observer_candidates_are_reviewed_not_automatically_active(runtime):
    runtime.register(
        "business", Step("work", AgentDefinition("work", handler=lambda value, context: value))
    )
    observer = attach_memory_observer(
        runtime,
        AgentDefinition(
            "observer",
            handler=lambda value, context: {
                "proposals": [
                    {
                        "content": "Require evidence before concluding",
                        "reason": "A reviewed pattern",
                    }
                ]
            },
        ),
    )
    run = runtime.submit("tenant", "task", "business", {}, idempotency_key="1")
    await runtime.execute("tenant", run["id"])
    await observer.drain("tenant", run["id"])
    memory = list(runtime.repository.scan("tenant", "memory"))
    assert len(memory) == 1
    assert memory[0]["state"] == "proposed"
    assert len(list(runtime.repository.scan("tenant", "run"))) == 2


def test_scoped_cache_isolation(tmp_path):
    from app.harness.kernel import Scope

    factory = ScopedCache(str(tmp_path), "v1")
    first = factory(Scope("tenant", "task", "run1"))
    second = factory(Scope("tenant", "task", "run2"))
    key = (("model",), "same-input")
    first.set({key: ({"value": "private"}, 60)})
    assert first.get([key])[key] == {"value": "private"}
    assert second.get([key]) == {}


async def test_no_supervisor_step_can_be_reassigned(runtime):
    executed = []
    small = AgentDefinition(
        "small", handler=lambda value, context: executed.append("small") or value
    )
    large = AgentDefinition(
        "large", handler=lambda value, context: executed.append("large") or value
    )
    runtime.register("test", Step("work", small, approval=Approval.before(), alternatives=(large,)))
    run = runtime.submit("tenant", "task", "test", {"value": 1}, idempotency_key="1")
    assert (await runtime.execute("tenant", run["id"]))["status"] == "waiting"
    approval = next(runtime.repository.scan("tenant", "approval"))
    runtime.approve(
        "tenant",
        approval["id"],
        actor="human",
        roles=["approver"],
        edited={"agent": "large", "inputs": {"value": 2}},
    )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"value": 2}
    assert executed == ["large"]
    assert len(list(runtime.repository.scan("tenant", "memory"))) == 1
