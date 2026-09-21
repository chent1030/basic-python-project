from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool


def adapt_interpreter(middleware: Any, tools: list[Any], definition: Any, context: Any) -> Any:
    from langchain_quickjs import CodeInterpreterMiddleware

    if not isinstance(middleware, CodeInterpreterMiddleware):
        return middleware
    if middleware._max_ptc_calls is None:
        raise ValueError("Interpreter host-call budget must be finite")
    configured = middleware._ptc or []
    if (
        configured
        and middleware._subagents
        and (definition.subagents or definition.general_purpose)
    ):
        raise ValueError(
            "Separate PTC tools from interruptible subagent dispatch to prevent replayed effects"
        )
    available = {tool.name: tool for tool in tools if isinstance(tool, BaseTool)}
    selected = []
    for item in configured:
        original = item if isinstance(item, BaseTool) else available.get(item)
        if original is None:
            raise ValueError("PTC requires an explicitly registered BaseTool")
        context.runtime.policy.check_tool(original.name)
        if definition.interrupt_on.get(original.name):
            raise ValueError(
                "SDK PTC bypasses per-tool HITL; use the normal tool path for this tool"
            )
        selected.append(_audit(original, context))
    return CodeInterpreterMiddleware(
        memory_limit=middleware._memory_limit,
        timeout=middleware._timeout,
        max_ptc_calls=middleware._max_ptc_calls,
        tool_name=middleware._tool_name,
        max_result_chars=middleware._max_result_chars,
        capture_console=middleware._capture_console,
        subagents=middleware._subagents,
        ptc=selected or None,
        mode=middleware._mode,
        max_snapshot_bytes=middleware._max_snapshot_bytes,
        snapshot_signing_key=middleware._snapshot_signing_key,
    )


def _audit(original: BaseTool, context: Any) -> BaseTool:
    async def invoke(**arguments: Any) -> Any:
        context.runtime.policy.check_tool(original.name)
        context.emit("ptc.tool.started", {"tool": original.name})
        result = await original.ainvoke(arguments)
        context.emit("ptc.tool.completed", {"tool": original.name})
        return result

    return StructuredTool(
        name=original.name,
        description=original.description,
        args_schema=original.args_schema,
        coroutine=invoke,
    )
