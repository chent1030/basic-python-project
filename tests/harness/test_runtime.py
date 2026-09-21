import asyncio

import pytest
from pydantic import BaseModel

from app.harness.kernel import (
    AgentDefinition,
    Approval,
    Condition,
    Input,
    Loop,
    Memory,
    Output,
    Parallel,
    Retry,
    Runtime,
    Sequence,
    SQLiteRepository,
    Step,
    Supervisor,
)
from app.harness.kernel.domain.models import Conflict, Forbidden


@pytest.fixture
def runtime(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")
    instance = Runtime(repository, workspace_root=tmp_path / "workspaces")
    yield instance
    repository.close()


def agent(name, handler, **kwargs):
    return AgentDefinition(name=name, handler=handler, **kwargs)


def submit(runtime, workflow="test", inputs=None, key="first", task="task"):
    return runtime.submit("tenant", task, workflow, inputs or {}, idempotency_key=key)


async def test_sequence_bindings_no_coordinator(runtime):
    calls = []

    async def extract(value, context):
        calls.append(context.scope.invocation_id)
        return {"items": value["document"].split()}

    async def count(value, context):
        calls.append(context.scope.invocation_id)
        return len(value["items"])

    runtime.register(
        "test",
        Sequence(
            Step("extract", agent("extract", extract)),
            Step("count", agent("count", count), {"items": Output("extract", "items")}),
        ),
    )
    run = submit(runtime, inputs={"document": "a b c"})
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == 3
    assert len(calls) == 2
    assert (await runtime.execute("tenant", run["id"]))["output"] == 3
    assert len(calls) == 2


async def test_parallel_overlaps_and_limits(runtime):
    active = 0
    peak = 0

    async def work(value, context):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        context.workspace.write("result.txt", context.scope.invocation_id)
        await asyncio.sleep(0.025)
        assert context.workspace.read("result.txt") == context.scope.invocation_id
        active -= 1
        return context.scope.invocation_id

    runtime.register(
        "test",
        Parallel(
            *(Step(f"step_{index}", agent("worker", work)) for index in range(5)), max_concurrency=2
        ),
    )
    run = submit(runtime)
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert peak == 2
    assert len(set(result["output"]["results"])) == 5


@pytest.mark.parametrize(
    "policy,status", [("require_all", "failed"), ("allow_partial", "succeeded")]
)
async def test_parallel_failure_policy(runtime, policy, status):
    def fail(value, context):
        raise ValueError("expected")

    runtime.register(
        "test",
        Parallel(
            Step("bad", agent("bad", fail)),
            Step("ok", agent("ok", lambda value, context: "ok")),
            failure_policy=policy,
        ),
    )
    result = await runtime.execute("tenant", submit(runtime)["id"])
    assert result["status"] == status
    if status == "succeeded":
        assert result["output"]["errors"][0]["error_type"] == "ValueError"


async def test_optional_before_and_after_approval(runtime):
    calls = []
    definition = agent("work", lambda value, context: calls.append(value) or value)
    runtime.register(
        "test",
        Sequence(
            Step("before", definition, approval=Approval.before()),
            Step("after", definition, Output("before"), approval=Approval.after()),
        ),
    )
    run = submit(runtime, inputs={"value": 1})
    assert (await runtime.execute("tenant", run["id"]))["status"] == "waiting"
    assert calls == []
    approval = next(runtime.repository.scan("tenant", "approval"))
    with pytest.raises(Forbidden):
        runtime.approve("tenant", approval["id"], actor="user", roles=["viewer"])
    runtime.approve("tenant", approval["id"], actor="user", roles=["approver"], edited={"value": 2})
    assert (await runtime.execute("tenant", run["id"]))["status"] == "waiting"
    assert calls == [{"value": 2}, {"value": 2}]
    approval = next(
        record
        for record in runtime.repository.scan("tenant", "approval")
        if record["state"] == "pending"
    )
    runtime.approve("tenant", approval["id"], actor="user", roles=["approver"], edited={"value": 3})
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"value": 3}
    assert len(calls) == 2


async def test_restart_reuses_completed_steps(runtime, tmp_path):
    calls = []
    definition = agent("work", lambda value, context: calls.append(1) or value)
    composition = Sequence(
        Step("first", definition), Step("second", definition, approval=Approval.before())
    )
    runtime.register("test", composition)
    run = submit(runtime)
    await runtime.execute("tenant", run["id"])
    restarted = Runtime(
        SQLiteRepository(tmp_path / "state.sqlite"), workspace_root=tmp_path / "workspaces"
    )
    restarted.register("test", composition)
    approval = next(restarted.repository.scan("tenant", "approval"))
    restarted.approve("tenant", approval["id"], actor="human", roles=["approver"])
    result = await restarted.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert calls == [1, 1]
    restarted.repository.close()


async def test_supervisor_edited_dispatch_proposes_memory(runtime):
    executed = []

    def decide(value, context):
        if value["history"]:
            return {"action": "finish", "output": value["history"][-1]["result"]}
        return {
            "action": "delegate",
            "calls": [
                {"agent": "small", "inputs": {"job": 1}, "reason": "simple task"},
            ],
        }

    small = agent("small", lambda value, context: executed.append("small") or value)
    large = agent("large", lambda value, context: executed.append("large") or value)
    runtime.register(
        "test",
        Supervisor(
            "supervise", agent("main", decide), (small, large), approval=Approval.every_delegation()
        ),
    )
    run = submit(runtime)
    assert (await runtime.execute("tenant", run["id"]))["status"] == "waiting"
    assert executed == []
    approval = next(runtime.repository.scan("tenant", "approval"))
    edited = {
        "action": "delegate",
        "calls": [
            {"agent": "large", "inputs": {"job": 2}, "reason": "human requires deeper analysis"},
        ],
    }
    runtime.approve("tenant", approval["id"], actor="human", roles=["approver"], edited=edited)
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert executed == ["large"]
    memories = list(runtime.repository.scan("tenant", "memory"))
    assert len(memories) == 1
    assert Memory(runtime.repository).search("tenant", "test") == []
    Memory(runtime.repository).review(
        "tenant",
        memories[0]["id"],
        actor="reviewer",
        roles=["memory_reviewer"],
        accept=True,
        expected_version=1,
    )
    assert len(Memory(runtime.repository).search("tenant", "test")) == 1


async def test_condition_loop_and_retry(runtime):
    attempts = 0

    async def transient(value, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary")
        return {"count": value["count"] + 1}

    body = Step(
        "increment", agent("increment", transient, retry=Retry(2, 0, idempotent=True)), Input()
    )
    runtime.register(
        "test",
        Condition(
            "select",
            lambda value, outputs: True,
            Loop("loop", body, lambda value: value["count"] == 2),
            body,
        ),
    )
    result = await runtime.execute("tenant", submit(runtime, inputs={"count": 0})["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"count": 2}
    assert attempts == 3


async def test_idempotency_and_tenant_scope(runtime):
    runtime.register("test", Step("work", agent("work", lambda value, context: value)))
    first = submit(runtime, inputs={"input": 1})
    assert submit(runtime, inputs={"input": 1})["id"] == first["id"]
    assert submit(runtime, key="second")["id"] != first["id"]
    with pytest.raises(Conflict):
        submit(runtime, inputs={"input": 2})
    with pytest.raises(Forbidden):
        runtime.get("another", first["id"])


async def test_cancel_running_requires_no_false_success(runtime):
    started = asyncio.Event()

    async def work(value, context):
        started.set()
        await asyncio.sleep(100)

    runtime.register("test", Step("work", agent("work", work)))
    run = submit(runtime)
    execution = asyncio.create_task(runtime.execute("tenant", run["id"]))
    await started.wait()
    with pytest.raises(Conflict):
        await runtime.execute("tenant", run["id"])
    runtime.cancel("tenant", run["id"], "human")
    result = await execution
    assert result["status"] == "cancelled"
    invocation = next(runtime.repository.scan("tenant", "invocation"))
    assert invocation["status"] == "uncertain"


async def test_output_contract_failure(runtime):
    class Report(BaseModel):
        count: int

    runtime.register(
        "test",
        Step(
            "work", agent("work", lambda value, context: {"count": "wrong"}, output_schema=Report)
        ),
    )
    result = await runtime.execute("tenant", submit(runtime)["id"])
    assert result["status"] == "failed"
    assert result["error_type"] == "ValidationError"
