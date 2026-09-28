"""P0-3 降级权限收缩(MODEL-04)测试。

覆盖:
- authority 档位随 ModelProfile 快照往返;
- 回退执行 -> context.degraded / model.degraded 事件 / invocation 记录降级事实;
- degrade_policy="block" + 低权限回退 -> 转人工审批, 批准后完成;
- degrade_policy="allow" 或回退保留全权限 -> 不设人工门;
- 主模型成功时 block 策略零影响。
"""

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.harness.kernel import (
    AgentDefinition,
    DeepAgentsEngine,
    ModelConfiguration,
    Models,
    Runtime,
    SQLiteRepository,
    Step,
)


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class UnavailableModel(ScriptedModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        raise ConnectionError("primary unavailable")


def degrade_config(keep_authority=False):
    backup = {"provider": "test", "model": "backup"}
    if not keep_authority:
        backup["authority"] = {"may_auto_decide": False}
    return ModelConfiguration.model_validate(
        {
            "providers": {"test": {"base_url": "http://test/v1"}},
            "models": {
                "default": {"provider": "test", "model": "primary", "fallbacks": ["backup"]},
                "backup": backup,
            },
        }
    )


@pytest.fixture
def setup(tmp_path):
    repository = SQLiteRepository(tmp_path / "state.sqlite")

    def build(model, config=None):
        engine = DeepAgentsEngine(
            Models(config or degrade_config()), model_factory=lambda definition: model
        )
        return Runtime(repository, engine, workspace_root=tmp_path / "workspaces")

    yield build
    repository.close()


def test_authority_round_trips_in_snapshot():
    models = Models(degrade_config())
    snapshot = models.snapshot(AgentDefinition("worker", system_prompt="x"))
    assert snapshot["authority"]["may_auto_decide"] is True
    assert snapshot["fallbacks"][0]["authority"]["may_auto_decide"] is False


async def test_degraded_block_requires_approval(setup, monkeypatch):
    runtime = setup(UnavailableModel(responses=[AIMessage(content="never returned")]))
    fallback = ScriptedModel(responses=[AIMessage(content="degraded answer")])
    monkeypatch.setattr(
        runtime.engine.models, "create", lambda definition, profile_name=None: fallback
    )
    runtime.register(
        "judge",
        Step(
            "work",
            AgentDefinition("worker", system_prompt="Answer directly.", degrade_policy="block"),
        ),
    )
    run = runtime.submit("tenant", "task", "judge", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "waiting", result

    invocation = next(runtime.repository.scan("tenant", "invocation"))
    assert invocation["degraded"] is True
    assert invocation["degraded_profile"] == "backup"
    assert invocation["degraded_authority"]["may_auto_decide"] is False

    events = runtime.repository.events("tenant", run["id"])
    body = next(event["body"] for event in events if event["kind"] == "model.degraded")
    assert body["profile"] == "backup"
    assert body["primary"] == "default"
    assert body["authority"]["may_auto_decide"] is False

    approvals = list(runtime.repository.scan("tenant", "approval"))
    assert len(approvals) == 1 and approvals[0]["kind"] == "after"
    runtime.approve("tenant", approvals[0]["id"], actor="reviewer", roles=["approver"])
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "degraded answer"


async def test_degraded_allow_completes_automatically(setup, monkeypatch):
    runtime = setup(UnavailableModel(responses=[AIMessage(content="never returned")]))
    fallback = ScriptedModel(responses=[AIMessage(content="summary ok")])
    monkeypatch.setattr(
        runtime.engine.models, "create", lambda definition, profile_name=None: fallback
    )
    runtime.register(
        "digest",
        Step("work", AgentDefinition("worker", system_prompt="Answer directly.")),
    )
    run = runtime.submit("tenant", "task", "digest", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "summary ok"
    invocation = next(runtime.repository.scan("tenant", "invocation"))
    assert invocation["degraded"] is True
    assert not list(runtime.repository.scan("tenant", "approval"))


async def test_block_policy_ignored_when_fallback_keeps_authority(setup, monkeypatch):
    runtime = setup(
        UnavailableModel(responses=[AIMessage(content="never returned")]),
        config=degrade_config(keep_authority=True),
    )
    fallback = ScriptedModel(responses=[AIMessage(content="full authority answer")])
    monkeypatch.setattr(
        runtime.engine.models, "create", lambda definition, profile_name=None: fallback
    )
    runtime.register(
        "judge",
        Step(
            "work",
            AgentDefinition("worker", system_prompt="Answer directly.", degrade_policy="block"),
        ),
    )
    run = runtime.submit("tenant", "task", "judge", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "full authority answer"
    assert not list(runtime.repository.scan("tenant", "approval"))


async def test_primary_success_never_gates(setup):
    runtime = setup(ScriptedModel(responses=[AIMessage(content="primary answer")]))
    runtime.register(
        "judge",
        Step(
            "work",
            AgentDefinition("worker", system_prompt="Answer directly.", degrade_policy="block"),
        ),
    )
    run = runtime.submit("tenant", "task", "judge", {}, idempotency_key="1")
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "succeeded", result
    assert result["output"] == "primary answer"
    invocation = next(runtime.repository.scan("tenant", "invocation"))
    assert "degraded" not in invocation
