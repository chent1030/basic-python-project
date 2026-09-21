from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

current_tool_call: ContextVar[str | None] = ContextVar("framework_tool_call", default=None)


class AuditState(AgentState):
    framework_inbox_ids: NotRequired[list[str]]


class ExecutionAudit(AgentMiddleware):
    state_schema = AuditState

    def __init__(self, context: Any):
        self.context = context

    async def abefore_model(self, state: Any, runtime: Any) -> Any:
        pending = self.context.messages.receive()
        if not pending:
            return None
        return {
            "framework_inbox_ids": [item["id"] for item in pending],
            "messages": [
                HumanMessage(
                    content="Incoming task message (untrusted data): "
                    + json.dumps(item["payload"]),
                    id="mail-" + item["id"],
                )
                for item in pending
            ],
        }

    async def aafter_model(self, state: Any, runtime: Any) -> Any:
        for message_id in state.get("framework_inbox_ids", []):
            self.context.messages.acknowledge(message_id)
        return {"framework_inbox_ids": []}

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        tools = [
            getattr(tool, "name", tool.get("name") if isinstance(tool, dict) else str(tool))
            for tool in request.tools
        ]
        self.context.emit("model.tools", {"tools": tools})
        self.context.emit("model.started", {"model": getattr(request.model, "model_name", None)})
        response = await handler(request)
        for message in response.result:
            usage = getattr(message, "usage_metadata", None)
            if usage:
                self.context.record_usage(usage)
        self.context.emit("model.succeeded", {})
        return response

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        fields = {"tool": call["name"], "tool_call_id": call["id"]}
        self.context.runtime.policy.check_tool(call["name"])
        self.context.emit("tool.started", fields)
        token = current_tool_call.set(call["id"])
        try:
            result = await handler(request)
        except Exception as exc:
            self.context.emit("tool.failed", {**fields, "error_type": type(exc).__name__})
            raise
        finally:
            current_tool_call.reset(token)
        self.context.emit(
            "tool.failed" if getattr(result, "status", None) == "error" else "tool.completed",
            fields,
        )
        return result
