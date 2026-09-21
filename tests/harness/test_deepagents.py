import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.harness.kernel import (
    AgentDefinition,
    Approval,
    DeepAgentsEngine,
    ModelConfiguration,
    Models,
    Runtime,
    SQLiteRepository,
    Step,
    SubAgent,
)


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def model_config():
    return ModelConfiguration.model_validate(
        {
            "providers": {"test": {"base_url": "http://localhost:8000/v1"}},
            "models": {"default": {"provider": "test", "model": "offline-test"}},
        }
    )


@pytest.fixture
def setup(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")

    def build(model):
        engine = DeepAgentsEngine(Models(model_config()), model_factory=lambda definition: model)
        return Runtime(repository, engine, workspace_root=tmp_path / "workspaces")

    yield build
    repository.close()


async def test_real_sdk_single_without_hidden_subagent(setup):
    runtime = setup(ScriptedModel(responses=[AIMessage(content="real graph output")]))
    definition = AgentDefinition(name="worker", system_prompt="Answer directly. Do not delegate.")
    runtime.register("single", Step("work", definition))
    run = runtime.submit("tenant", "task", "single", {"question": "test"}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "real graph output"
    invocations = list(runtime.repository.scan("tenant", "invocation"))
    assert len(invocations) == 1
    events = runtime.repository.events("tenant", run["id"])
    effective = next(event["body"] for event in events if event["kind"] == "engine.capabilities")
    assert effective["subagents"] == []
    assert any(event["kind"] == "engine.stream" for event in events)


async def test_real_sdk_tool_hitl_and_resume(setup):
    calls = []

    @tool
    def publish_report(text: str) -> str:
        """Publish a reviewed report."""
        calls.append(text)
        return "published"

    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish_report",
                        "args": {"text": "draft"},
                        "id": "publish_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="complete"),
        ]
    )
    runtime = setup(model)
    definition = AgentDefinition(
        name="publisher",
        system_prompt="Publish after review.",
        tools=(publish_report,),
        interrupt_on={"publish_report": True},
    )
    runtime.register("hitl", Step("work", definition))
    run = runtime.submit("tenant", "task", "hitl", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "waiting", result
    assert calls == []
    approval = next(runtime.repository.scan("tenant", "approval"))
    runtime.approve(
        "tenant",
        approval["id"],
        actor="reviewer",
        roles=["approver"],
        edited={
            "decisions": [
                {
                    "type": "edit",
                    "edited_action": {
                        "name": "publish_report",
                        "args": {"text": "reviewed"},
                    },
                }
            ]
        },
    )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert calls == ["reviewed"]


async def test_real_sdk_native_subagent_goes_through_runtime(setup):
    scopes = []

    async def child_work(value, context):
        scopes.append(context.scope)
        return {"answer": "child output"}

    child = AgentDefinition(name="child", handler=child_work)
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Do a bounded task",
                            "subagent_type": "child",
                        },
                        "id": "delegate_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="complete"),
        ]
    )
    runtime = setup(model)
    definition = AgentDefinition(
        name="parent",
        system_prompt="Delegate to child then summarize.",
        subagents=(SubAgent(child, "A bounded worker"),),
    )
    runtime.register("native", Step("work", definition))
    run = runtime.submit("tenant", "task", "native", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert len(scopes) == 1
    records = list(runtime.repository.scan("tenant", "invocation"))
    assert len(records) == 2
    parent = next(record for record in records if record["agent"] == "parent")
    child_record = next(record for record in records if record["agent"] == "child")
    assert child_record["parent_id"] == parent["id"]


@pytest.mark.parametrize("dynamic", [False, True])
async def test_native_and_interpreter_dispatch_approval_cannot_bypass(setup, dynamic):
    from langchain_quickjs import CodeInterpreterMiddleware

    executed = []
    child = AgentDefinition(
        "child",
        handler=lambda value, context: executed.append(1) or "done",
        approval=Approval.before(),
    )
    name = "eval" if dynamic else "task"
    arguments = (
        {"code": 'await task({description: "bounded work", subagentType: "child"})'}
        if dynamic
        else {"description": "bounded work", "subagent_type": "child"}
    )
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": arguments, "id": "delegate", "type": "tool_call"}
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    runtime = setup(model)
    definition = AgentDefinition(
        "parent",
        system_prompt="Delegate a bounded task.",
        subagents=(SubAgent(child, "Child worker"),),
        middleware=(CodeInterpreterMiddleware(),) if dynamic else (),
    )
    runtime.register("native", Step("work", definition))
    run = runtime.submit("tenant", "task", "native", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "waiting", result
    assert executed == []
    approvals = list(runtime.repository.scan("tenant", "approval"))
    assert len(approvals) == 1
    runtime.approve("tenant", approvals[0]["id"], actor="reviewer", roles=["approver"])
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert executed == [1]


async def test_fork_has_isolated_identity_and_inherited_messages(setup):
    contexts = []

    def work(value, context):
        contexts.append(context)
        return "forked"

    child = AgentDefinition("forked", handler=work)
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "continue",
                            "subagent_type": "forked",
                        },
                        "id": "fork",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    runtime = setup(model)
    runtime.register(
        "fork",
        Step(
            "work",
            AgentDefinition(
                "parent",
                system_prompt="Delegate by forking.",
                subagents=(
                    SubAgent(child, "Continue with context", mode="fork", allow_context_fork=True),
                ),
            ),
        ),
    )
    run = runtime.submit(
        "tenant", "task", "fork", {"context": "original request"}, idempotency_key="1"
    )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert contexts[0].inherited_messages
    assert "original request" in str(contexts[0].inherited_messages)
    assert contexts[0].scope.invocation_id != "root"


async def test_leaf_disables_native_and_interpreter_hidden_delegation(setup):
    from langchain_quickjs import CodeInterpreterMiddleware

    runtime = setup(ScriptedModel(responses=[AIMessage(content="done")]))
    runtime.register(
        "leaf",
        Step(
            "work",
            AgentDefinition(
                "leaf",
                system_prompt="Do not delegate.",
                planning=False,
                middleware=(CodeInterpreterMiddleware(),),
            ),
        ),
    )
    run = runtime.submit("tenant", "task", "leaf", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    events = runtime.repository.events("tenant", run["id"])
    names = next(event["body"]["tools"] for event in events if event["kind"] == "model.tools")
    assert "task" not in names
    assert "write_todos" not in names


async def test_repeated_tool_interrupts_and_rejection(setup):
    executed = []

    @tool
    def publish(text: str) -> str:
        """Publish text."""
        executed.append(text)
        return text

    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish",
                        "args": {"text": "first"},
                        "id": "first",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish",
                        "args": {"text": "second"},
                        "id": "second",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    runtime = setup(model)
    runtime.register(
        "hitl",
        Step(
            "work",
            AgentDefinition(
                "publisher",
                system_prompt="Publish after approval.",
                tools=(publish,),
                interrupt_on={"publish": True},
            ),
        ),
    )
    run = runtime.submit("tenant", "task", "hitl", {}, idempotency_key="1")
    for _ in range(2):
        result = await runtime.execute("tenant", run["id"])
        assert result["status"] == "waiting", result
        approval = next(
            record
            for record in runtime.repository.scan("tenant", "approval")
            if record["state"] == "pending"
        )
        runtime.approve(
            "tenant",
            approval["id"],
            actor="human",
            roles=["approver"],
            edited={"decisions": [{"type": "reject", "message": "Do not publish"}]},
        )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert executed == []


@pytest.mark.parametrize("dynamic", [False, True])
async def test_identical_parallel_native_calls_remain_distinct(setup, dynamic):
    from langchain_quickjs import CodeInterpreterMiddleware

    executed = []
    child = AgentDefinition(
        "child",
        handler=lambda value, context: executed.append(context.scope.invocation_id) or "done",
        approval=Approval.before(),
    )
    calls = [
        {
            "name": "task",
            "args": {"description": "same", "subagent_type": "child"},
            "id": f"same_{index}",
            "type": "tool_call",
        }
        for index in range(2)
    ]
    if dynamic:
        calls = [
            {
                "name": "eval",
                "args": {
                    "code": 'await Promise.all([task({description:"same",'
                    'subagentType:"child"}), task({description:"same", subagentType:"child"})])'
                },
                "id": "eval_parallel",
                "type": "tool_call",
            }
        ]
    runtime = setup(
        ScriptedModel(
            responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="done")]
        )
    )
    runtime.register(
        "test",
        Step(
            "work",
            AgentDefinition(
                "parent",
                system_prompt="Delegate two independent requests.",
                subagents=(SubAgent(child, "Child worker"),),
                middleware=(CodeInterpreterMiddleware(),) if dynamic else (),
            ),
        ),
    )
    run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    for _ in range(4):
        result = await runtime.execute("tenant", run["id"])
        if result["status"] == "succeeded":
            break
        assert result["status"] == "waiting", result
        for approval in runtime.repository.scan("tenant", "approval"):
            if approval["state"] == "pending":
                runtime.approve("tenant", approval["id"], actor="human", roles=["approver"])
    assert result["status"] == "succeeded", result
    assert len(set(executed)) == 2


async def test_real_sdk_structured_output(setup):
    from pydantic import BaseModel

    class Result(BaseModel):
        count: int

    runtime = setup(
        ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "Result",
                            "args": {"count": 3},
                            "id": "result",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )
    runtime.register(
        "typed",
        Step(
            "work",
            AgentDefinition(
                "typed",
                system_prompt="Return a Result object",
                output_schema=Result,
            ),
        ),
    )
    run = runtime.submit("tenant", "task", "typed", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"count": 3}


async def test_model_fallback_stays_at_model_call_boundary(setup, monkeypatch):
    class UnavailableModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            raise ConnectionError("primary unavailable")

    runtime = setup(UnavailableModel(responses=[AIMessage(content="never returned")]))
    runtime.engine.models.config = ModelConfiguration.model_validate(
        {
            "providers": {"test": {"base_url": "http://test/v1"}},
            "models": {
                "default": {"provider": "test", "model": "primary", "fallbacks": ["backup"]},
                "backup": {"provider": "test", "model": "backup"},
            },
        }
    )
    fallback = ScriptedModel(responses=[AIMessage(content="fallback response")])
    monkeypatch.setattr(
        runtime.engine.models, "create", lambda definition, profile_name=None: fallback
    )
    runtime.register(
        "test", Step("work", AgentDefinition("worker", system_prompt="Answer directly."))
    )
    run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "fallback response"
    assert next(runtime.repository.scan("tenant", "invocation"))["attempt"] == 1
