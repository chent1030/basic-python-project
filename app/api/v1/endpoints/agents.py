"""AI Agent 框架端点 —— 统一入口 trigger / chat + 状态监控。

所有端点复用 agent_gateway(单例,lifespan 启停):
- GET  /agents                         列出所有已注册 agent + 拓扑/模式
- GET  /agents/{name}                  单个 agent 详情
- POST /agents/{name}/trigger          一次性触发(非流式)
- POST /agents/{name}/trigger/stream   流式触发(SSE)
- POST /agents/{name}/chat             持续对话(body 带 session_id)
- POST /agents/{name}/chat/stream      流式持续对话(SSE)
- GET  /agents/{name}/runs             运行记录(状态监控)
- GET  /agents/{name}/runs/{run_id}/tree  单次运行的调用树
- GET  /agents/{name}/sessions/{sid}   会话历史
"""
from __future__ import annotations

from collections.abc import AsyncIterable

from fastapi import APIRouter, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from app.ai import agent_gateway
from app.ai.registry import registry
from app.ai.runs import run_store
from app.ai.session import session_store

router = APIRouter(prefix="/agents", tags=["agents"])


# ----------------------------------------------------------------- 列表/详情
@router.get("")
async def list_agents() -> dict[str, object]:
    """列出所有已注册 agent 及其拓扑/模式/工具等元信息。"""
    return {"agents": agent_gateway.list_agents()}


@router.get("/{name}")
async def get_agent(name: str) -> dict[str, object]:
    """单个 agent 详情。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"agent '{name}' 未注册")
    return agent_gateway.get_agent_info(name)


# ----------------------------------------------------------------- 一次性触发
class TriggerIn(BaseModel):
    message: str
    user_id: str | None = None
    session_id: str | None = None  # 可选:给 trigger 也附一个 session 上下文


@router.post("/{name}/trigger")
async def trigger(name: str, body: TriggerIn) -> dict[str, object]:
    """一次性触发一个 agent(非流式)。"""
    result = await agent_gateway.trigger(
        name, body.message, source="api", user_id=body.user_id, session_id=body.session_id
    )
    return {
        "agent": name,
        "output": result.output,
        "tokens": result.tokens,
        "extra": result.extra,
    }


@router.post("/{name}/trigger/stream", response_class=EventSourceResponse)
async def trigger_stream(name: str, body: TriggerIn) -> AsyncIterable[ServerSentEvent]:
    """流式触发(SSE)。"""
    async for chunk in agent_gateway.trigger_stream(
        name, body.message, source="api"
    ):
        yield ServerSentEvent(data=chunk, event="delta")
    yield ServerSentEvent(data="[DONE]", event="done")


# ----------------------------------------------------------------- 持续对话
class ChatIn(BaseModel):
    message: str
    session_id: str
    user_id: str | None = None


@router.post("/{name}/chat")
async def chat(name: str, body: ChatIn) -> dict[str, object]:
    """持续对话:按 session_id 维护历史。"""
    result = await agent_gateway.chat(
        name, body.session_id, body.message, source="api", user_id=body.user_id
    )
    return {
        "agent": name,
        "session_id": body.session_id,
        "output": result.output,
        "tokens": result.tokens,
        "extra": result.extra,
    }


@router.post("/{name}/chat/stream", response_class=EventSourceResponse)
async def chat_stream(name: str, body: ChatIn) -> AsyncIterable[ServerSentEvent]:
    """流式持续对话(SSE)。"""
    async for chunk in agent_gateway.chat_stream(
        name, body.session_id, body.message, source="api"
    ):
        yield ServerSentEvent(data=chunk, event="delta")
    yield ServerSentEvent(data="[DONE]", event="done")


# ----------------------------------------------------------------- 状态监控
@router.get("/{name}/runs")
async def list_runs(
    name: str,
    limit: int = Query(50, ge=1, le=500),
    status: str | None = None,
    include_children: bool = Query(False, description="包含成员/子 agent 调用(复合拓扑)"),
) -> dict[str, object]:
    """某 agent 的运行记录(状态监控)。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"agent '{name}' 未注册")
    runs = await run_store.list_runs(
        name, limit=limit, status=status, include_children=include_children
    )
    return {"agent": name, "runs": runs}


@router.get("/{name}/runs/{run_id}/tree")
async def get_run_tree(name: str, run_id: str) -> dict[str, object]:
    """单次运行的完整调用树(多拓扑可看成员/子 agent 调用结构)。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"agent '{name}' 未注册")
    tree = await run_store.get_tree(run_id)
    return {"agent": name, **tree}


@router.get("/{name}/sessions/{session_id}")
async def get_session(name: str, session_id: str) -> dict[str, object]:
    """某会话的消息历史。"""
    if not registry.has(name):
        raise HTTPException(status_code=404, detail=f"agent '{name}' 未注册")
    history = await session_store.load_history(name, session_id, limit=500)
    return {"agent": name, "session_id": session_id, "messages": history}
