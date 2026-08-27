from __future__ import annotations

import asyncio

from app.harness.base import BaseAgent
from app.harness.communication import Blackboard
from app.harness.communication.blackboard_var import get_blackboard, use_blackboard
from app.harness.context import AgentResult, AgentRunContext


class _ProbeAgent(BaseAgent):
    name = "probe"

    async def _execute_topology(self, ctx: AgentRunContext) -> AgentResult:
        return AgentResult(output=f"{ctx.last_user_message}:{get_blackboard() is ctx.blackboard}")


class _ParentAgent(BaseAgent):
    name = "parent"


class _ReplacingMiddlewareAgent(_ProbeAgent):
    async def _run_middleware_before(self, ctx: AgentRunContext) -> AgentRunContext:
        return AgentRunContext(
            agent_name=ctx.agent_name,
            messages=ctx.messages,
            blackboard=Blackboard(),
        )

    async def _execute_topology(self, ctx: AgentRunContext) -> AgentResult:
        return AgentResult(output=f"{get_blackboard() is ctx.blackboard}:{self.context is ctx}")


class _ConcurrentProbeAgent(_ProbeAgent):
    async def _execute_topology(self, ctx: AgentRunContext) -> AgentResult:
        await asyncio.sleep(0)
        return AgentResult(
            output=(
                f"{ctx.last_user_message}:"
                f"{get_blackboard() is ctx.blackboard}:"
                f"{self.context is ctx}"
            )
        )


def test_nested_binding_restores_parent_blackboard() -> None:
    outer = Blackboard()
    inner = Blackboard()
    with use_blackboard(outer):
        assert get_blackboard() is outer
        with use_blackboard(inner):
            assert get_blackboard() is inner
        assert get_blackboard() is outer


def test_binding_restores_parent_after_exception() -> None:
    outer = Blackboard()
    inner = Blackboard()
    with use_blackboard(outer):
        try:
            with use_blackboard(inner):
                raise ValueError("expected")
        except ValueError:
            pass
        assert get_blackboard() is outer


async def test_member_gets_new_message_and_shared_blackboard() -> None:
    context = AgentRunContext(messages=[{"role": "user", "content": "old"}])
    result = await _ParentAgent()._run_member(_ProbeAgent, "new", context)
    assert result.output == "new:True"


async def test_parallel_context_bindings_do_not_leak() -> None:
    first = Blackboard()
    second = Blackboard()

    async def probe(board: Blackboard) -> bool:
        with use_blackboard(board):
            await asyncio.sleep(0)
            return get_blackboard() is board

    assert await asyncio.gather(probe(first), probe(second)) == [True, True]


async def test_middleware_replacement_rebinds_context_and_blackboard() -> None:
    result = await _ReplacingMiddlewareAgent().run("new")
    assert result.output == "True:True"


async def test_same_agent_instance_uses_task_local_context() -> None:
    agent = _ConcurrentProbeAgent()
    first, second = await asyncio.gather(agent.run("first"), agent.run("second"))
    assert {first.output, second.output} == {"first:True:True", "second:True:True"}


def test_extend_unique_is_atomic_and_deduplicates() -> None:
    board = Blackboard()
    board.extend_unique("images", [{"name": "a", "data": "1"}], identity=lambda x: x["name"])
    merged = board.extend_unique(
        "images",
        [{"name": "a", "data": "2"}, {"name": "b", "data": "3"}],
        identity=lambda x: x["name"],
    )
    assert merged == [{"name": "a", "data": "1"}, {"name": "b", "data": "3"}]
