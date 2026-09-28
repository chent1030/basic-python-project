from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.harness.kernel.infrastructure.guardrails import (
    GuardrailRules,
    ToolGuardrailMiddleware,
    Violation,
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


def search_docs(query: str, limit: int = 5) -> str:
    return f"results for {query}"


SEARCH = StructuredTool.from_function(
    search_docs, name="search_docs", description="Search documents"
)
DICT_TOOL = {
    "name": "write_file",
    "args_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "overwrite": {"type": "boolean"}},
        "required": ["path"],
        "additionalProperties": False,
    },
}


def request_for(name: str, args: Any, tools: list[Any]) -> SimpleNamespace:
    """Mirror ToolCallRequest: tool_call + resolved tool (None when unknown)."""
    resolved = next(
        (
            tool
            for tool in tools
            if (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None))
            == name
        ),
        None,
    )
    return SimpleNamespace(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=resolved,
        tools=tools,
    )


@pytest.fixture
def recorded() -> Recorder:
    return Recorder()


async def test_unknown_tool_returns_suggestions(recorded):
    guard = ToolGuardrailMiddleware(
        recorded, GuardrailRules(mode="fail"), tools=[SEARCH, DICT_TOOL]
    )
    request = request_for("search_doc", {"query": "x"}, [SEARCH, DICT_TOOL])
    result = await guard.awrap_tool_call(request, handler=None)
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "search_docs" in result.content
    assert "unknown_tool" in result.content
    assert "tool.rejected" in recorded.names()


async def test_denied_pattern_rejects_in_fail_mode(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="fail"))
    request = request_for("search_docs", {"query": "rm -rf /"}, [SEARCH])

    async def handler(request):
        return "executed"

    result = await guard.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "shell-destructive" in result.content
    assert "tool.rejected" in recorded.names()
    assert "tool.guardrail" in recorded.names()


async def test_warn_mode_audits_but_executes(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="warn"))
    request = request_for("search_docs", {"query": "DROP TABLE users"}, [SEARCH])

    async def handler(request):
        return "executed"

    assert await guard.awrap_tool_call(request, handler) == "executed"
    assert "tool.guardrail" in recorded.names()
    assert "tool.rejected" not in recorded.names()


async def test_schema_violations_report_fields(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="fail"))
    request = request_for("write_file", {"path": 123, "extra": "x"}, [DICT_TOOL])
    result = await guard.awrap_tool_call(request, handler=None)
    codes = {
        (item["code"], item.get("field"))
        for _, payload in recorded.events
        for item in payload.get("violations", [])
    }
    assert ("schema", "extra") in codes  # unknown field
    assert result.status == "error"

    request = request_for("write_file", {"overwrite": True}, [DICT_TOOL])
    await guard.awrap_tool_call(request, handler=None)
    codes = {
        (item["code"], item.get("field"))
        for _, payload in recorded.events
        for item in payload.get("violations", [])
    }
    assert ("schema", "path") in codes  # missing required


async def test_numeric_range_violation(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="fail"))
    request = request_for("search_docs", {"query": "ok", "limit": 99999}, [SEARCH])
    result = await guard.awrap_tool_call(request, handler=None)
    assert result.status == "error"
    assert "limit" in result.content


async def test_clean_call_passes_through(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="fail"))
    request = request_for("search_docs", {"query": "inspection report", "limit": 3}, [SEARCH])

    async def handler(request):
        return "ok"

    assert await guard.awrap_tool_call(request, handler) == "ok"
    assert recorded.names() == []


def test_rules_reject_unknown_keys(tmp_path):
    path = tmp_path / "guardrails.yaml"
    path.write_text(yaml.safe_dump({"mode": "fail", "unknown_section": {}}), encoding="utf-8")
    with pytest.raises(ValidationError):
        GuardrailRules.load(path)


def test_rules_load_from_file(tmp_path):
    path = tmp_path / "guardrails.yaml"
    path.write_text(
        yaml.safe_dump({"mode": "fail", "similarity_cutoff": 0.8, "denied_patterns": []}),
        encoding="utf-8",
    )
    rules = GuardrailRules.load(path)
    assert rules.mode == "fail"
    assert rules.similarity_cutoff == 0.8
    assert rules.denied_patterns == []


async def test_off_mode_skips_validation(recorded):
    guard = ToolGuardrailMiddleware(recorded, GuardrailRules(mode="off"))
    request = request_for("search_docs", {"query": "rm -rf /"}, [SEARCH])

    async def handler(request):
        return "executed"

    assert await guard.awrap_tool_call(request, handler) == "executed"
    assert recorded.names() == []


def test_violation_event_shape():
    event = Violation(code="pattern", message="m", rule="sql-drop").as_event()
    assert event == {"code": "pattern", "message": "m", "rule": "sql-drop"}
