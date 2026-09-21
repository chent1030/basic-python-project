from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import GetOp, PutOp, SearchOp
from langgraph.types import interrupt

from app.harness.kernel import (
    AgentDefinition,
    CompiledAgent,
    RemoteAgent,
    Resources,
    Runtime,
    SQLiteRepository,
    Step,
)
from app.harness.kernel.infrastructure.resources import DockerSandbox
from app.harness.kernel.infrastructure.store import ScopedStore


@pytest.fixture
def runtime(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")
    runtime = Runtime(repository, workspace_root=tmp_path / "workspaces")
    yield runtime
    repository.close()


async def test_compiled_graph_custom_interrupt_persists(runtime):
    class State(TypedDict):
        question: str
        answer: str

    def factory(context, saver):
        def ask(state):
            answer = interrupt({"question": state["question"]})
            return {"answer": answer}

        graph = StateGraph(State)
        graph.add_node("ask", ask)
        graph.add_edge(START, "ask")
        graph.add_edge("ask", END)
        return graph.compile(checkpointer=saver)

    definition = AgentDefinition("compiled", handler=CompiledAgent(factory))
    runtime.register("test", Step("work", definition))
    run = runtime.submit("tenant", "task", "test", {"question": "Continue?"}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "waiting", result
    approval = next(runtime.repository.scan("tenant", "approval"))
    interrupt_id = approval["payload"]["interrupts"][0]["id"]
    runtime.approve(
        "tenant",
        approval["id"],
        actor="human",
        roles=["approver"],
        edited={"responses": {interrupt_id: "yes"}},
    )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"]["answer"] == "yes"


def test_scoped_store_persistence(runtime):
    first = ScopedStore(runtime.repository, "tenant", "run1")
    second = ScopedStore(runtime.repository, "tenant", "run2")
    first.batch([PutOp(("files",), "report", {"text": "one"})])
    assert first.batch([GetOp(("files",), "report")])[0].value == {"text": "one"}
    assert second.batch([GetOp(("files",), "report")]) == [None]
    assert first.batch([SearchOp(("files",), query="one")])[0][0].key == "report"
    assert ScopedStore(runtime.repository, "other", "run1").batch([SearchOp(())]) == [[]]


async def test_readonly_resources_and_store_backend(runtime, tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "SKILL.md").write_text("immutable")

    def handler(value, context):
        backend = Resources({"/skills/": str(resources)}, persisted_files=True)(context)
        assert not backend.read("/skills/SKILL.md").error
        assert backend.write("/skills/new.md", "denied").error
        assert backend.edit("/skills/SKILL.md", "immutable", "changed").error
        assert not backend.write("/persisted/result.txt", "durable").error
        assert not backend.read("/persisted/result.txt").error
        return "ok"

    runtime.register("test", Step("work", AgentDefinition("resources", handler=handler)))
    run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert (resources / "SKILL.md").read_text() == "immutable"


def test_docker_restricts_host_network_and_resources(tmp_path, monkeypatch):
    commands = []

    def execute(args, **kwargs):
        commands.append(args)
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"sandbox output")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", execute)
    sandbox = DockerSandbox(tmp_path, image="python@sha256:" + "a" * 64)
    result = sandbox.execute("echo safe")
    assert result.output == "sandbox output"
    assert "--network=none" in commands[0]
    assert "--read-only" in commands[0]
    assert "--cap-drop=ALL" in commands[0]
    assert commands[1][:3] == ["docker", "rm", "-f"]
    with pytest.raises(ValueError):
        DockerSandbox(tmp_path, image="python:latest")


async def test_remote_lifecycle_and_idempotent_collection(runtime):
    creations = []

    class Threads:
        async def create(self, **kwargs):
            return {"thread_id": kwargs["thread_id"]}

        async def get_state(self, thread_id):
            return {"values": {"answer": "remote output"}}

    class Runs:
        async def create(self, thread_id, graph_id, **kwargs):
            creations.append(kwargs)
            return {"run_id": "remote-run"}

        async def get(self, thread_id, run_id):
            return {"status": "success"}

    remote = RemoteAgent(
        "http://remote",
        "graph",
        client_factory=lambda: SimpleNamespace(
            threads=Threads(),
            runs=Runs(),
        ),
    )
    runtime.register("test", Step("remote", AgentDefinition("remote", handler=remote)))
    run = runtime.submit("tenant", "task", "test", {"messages": []}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == {"answer": "remote output"}
    await runtime.execute("tenant", run["id"])
    assert len(creations) == 1
    assert next(runtime.repository.scan("tenant", "remote"))["phase"] == "succeeded"


async def test_mcp_allowlist_and_session_lifecycle(runtime, monkeypatch):
    from langchain_core.tools import StructuredTool

    from app.harness.kernel import MCPTools

    lifecycle = []

    async def echo(text: str):
        return text

    class Client:
        def __init__(self, *args, **kwargs):
            return

        @asynccontextmanager
        async def session(self, name):
            lifecycle.append("open")
            try:
                yield "session"
            finally:
                lifecycle.append("closed")

    async def tools(session):
        return [StructuredTool.from_function(coroutine=echo, name="echo", description="Echo input")]

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", Client)
    monkeypatch.setattr("langchain_mcp_adapters.tools.load_mcp_tools", tools)

    async def handler(value, context):
        toolkit = MCPTools(
            {"local": {"transport": "streamable_http", "url": "http://mcp"}}, ("local__echo",)
        )
        async with toolkit(context) as selected:
            assert [tool.name for tool in selected] == ["local__echo"]
            return await selected[0].ainvoke({"text": "actual tool call"})

    runtime.register("test", Step("mcp", AgentDefinition("mcp", handler=handler)))
    run = runtime.submit("tenant", "task", "test", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "actual tool call"
    assert lifecycle == ["open", "closed"]
