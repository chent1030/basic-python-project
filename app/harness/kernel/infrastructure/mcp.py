from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool

from ..domain.models import Forbidden, digest


@dataclass(frozen=True)
class MCPTools:
    connections: dict[str, dict[str, Any]]
    allowed_tools: tuple[str, ...]
    allow_stdio: bool = False
    timeout_seconds: float = 30

    def snapshot(self) -> dict[str, Any]:
        return {
            "servers": list(self.connections),
            "allowed_tools": self.allowed_tools,
            "configuration_hash": digest(self.connections),
            "allow_stdio": self.allow_stdio,
            "timeout": self.timeout_seconds,
        }

    @asynccontextmanager
    async def __call__(self, context: Any):
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools

        connections = {}
        for name, raw in self.connections.items():
            connection = dict(raw)
            if connection.get("transport") == "stdio" and not self.allow_stdio:
                raise Forbidden("MCP stdio requires explicit host-process authorization")
            for field in ("headers", "env"):
                if field in connection:
                    connection[field] = {
                        key: os.environ[value[4:]] if value.startswith("env:") else value
                        for key, value in connection[field].items()
                    }
            connections[name] = connection
        client = MultiServerMCPClient(connections, handle_tool_errors=False)
        selected = []
        async with AsyncExitStack() as stack:
            for server_name in connections:
                session = await stack.enter_async_context(client.session(server_name))
                async with asyncio.timeout(self.timeout_seconds):
                    tools = await load_mcp_tools(session)
                for original in tools:
                    qualified = f"{server_name}__{original.name}"
                    if qualified in self.allowed_tools:
                        selected.append(self._wrap(original, qualified, context))
            if {tool.name for tool in selected} != set(self.allowed_tools):
                raise ValueError("Configured MCP tool allowlist does not match discovered tools")
            yield selected

    def _wrap(self, original: Any, name: str, context: Any) -> StructuredTool:
        async def execute(**arguments: Any) -> Any:
            context.emit("mcp.tool.started", {"tool": name})
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await original.ainvoke(arguments)
                context.emit("mcp.tool.succeeded", {"tool": name})
                return result
            except Exception as exc:
                context.emit("mcp.tool.failed", {"tool": name, "error_type": type(exc).__name__})
                raise

        return StructuredTool(
            name=name,
            description=original.description,
            args_schema=original.args_schema,
            coroutine=execute,
        )
