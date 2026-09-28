from __future__ import annotations

import difflib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field

# P0-2 (Harness合规修复开发计划): parameter-level tool guardrails.
# Layers implemented (standard MODEL-09/11/12/14, SEC-16/19/21):
#   1. args schema validation (required/type/unknown-field/enum)
#   2. dangerous pattern scan over all string material in args
#   3. unknown tool name -> closest registered tool suggestions
#   4. numeric range limits for sensitive parameter names
# Modes: "warn" (audit only, gray release), "fail" (reject and feed the
# violations back to the model), "off".

DEFAULT_PATTERNS: list[dict[str, str]] = [
    {
        "id": "shell-destructive",
        "pattern": r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)",
        "description": "recursive force delete",
    },
    {
        "id": "shell-pipe-exec",
        "pattern": r"(curl|wget)[^|]{0,200}\|\s*(ba)?sh",
        "description": "pipe download into shell",
    },
    {
        "id": "sql-drop",
        "pattern": r"DROP\s+(TABLE|DATABASE|SCHEMA)",
        "description": "destructive SQL",
    },
    {"id": "sql-truncate", "pattern": r"TRUNCATE\s+TABLE", "description": "destructive SQL"},
    {"id": "markup-script-injection", "pattern": r"<script", "description": "script tag injection"},
    {"id": "jndi-lookup", "pattern": r"\$\{jndi:", "description": "JNDI lookup injection"},
    {
        "id": "path-traversal",
        "pattern": r"\.\.[\\/]\.\.[\\/]",
        "description": "parent-directory traversal",
    },
]

DEFAULT_NUMERIC_LIMITS: dict[str, dict[str, float]] = {
    "timeout": {"min": 0, "max": 600},
    "timeout_seconds": {"min": 0, "max": 600},
    "file_size": {"min": 0, "max": 10_000_000},
    "max_bytes": {"min": 0, "max": 10_000_000},
    "memory_mb": {"min": 0, "max": 4096},
    "limit": {"min": 0, "max": 10000},
    "max_results": {"min": 0, "max": 10000},
}


class PatternRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    pattern: str
    description: str = ""

    def compile(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)


class NumericLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = None
    max: float | None = None


class GuardrailRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "warn"
    similarity_cutoff: float = Field(default=0.6, ge=0.0, le=1.0)
    denied_patterns: list[PatternRule] = Field(
        default_factory=lambda: [PatternRule(**item) for item in DEFAULT_PATTERNS]
    )
    numeric_limits: dict[str, NumericLimit] = Field(
        default_factory=lambda: {
            name: NumericLimit(**limits) for name, limits in DEFAULT_NUMERIC_LIMITS.items()
        }
    )

    @classmethod
    def load(cls, path: str | Path | None = None) -> GuardrailRules:
        location = Path(path or os.environ.get("GUARDRAILS_CONFIG", "config/guardrails.yaml"))
        if not location.exists():
            return cls()
        payload = yaml.safe_load(location.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Guardrail config must be a mapping: {location}")
        return cls.model_validate(payload)

    def patterns(self) -> list[tuple[PatternRule, re.Pattern[str]]]:
        return [(rule, rule.compile()) for rule in self.denied_patterns]


_JSON_TYPE_OF: dict[type, str] = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    list: "array",
    dict: "object",
}


class Violation(BaseModel):
    code: str
    message: str
    field: str | None = None
    rule: str | None = None

    def as_event(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return tool.get("name")
    return getattr(tool, "name", None)


def _tool_schema(tool: Any) -> dict[str, Any] | None:
    if isinstance(tool, dict):
        schema = tool.get("args_schema") or tool.get("parameters") or tool.get("input_schema")
        return schema if isinstance(schema, dict) else None
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        try:
            schema = args_schema.model_json_schema()
        except Exception:
            schema = None
        if isinstance(schema, dict):
            return schema
    schema = getattr(tool, "args", None)
    return schema if isinstance(schema, dict) else None


class ToolGuardrailMiddleware(AgentMiddleware):
    """Rejects or flags tool calls before execution based on external rules."""

    def __init__(
        self,
        context: Any,
        rules: GuardrailRules | None = None,
        tools: Any = (),
    ):
        self.context = context
        self.rules = rules if rules is not None else GuardrailRules.load()
        self.inventory: dict[str, Any] = {}
        for tool in tools:
            name = _tool_name(tool)
            if name:
                self.inventory[name] = tool

    # -- validation helpers -------------------------------------------------

    def _schema_violations(self, schema: dict[str, Any], args: dict[str, Any]) -> list[Violation]:
        violations: list[Violation] = []
        properties = schema.get("properties")
        if properties is None:
            properties = {
                key: value
                for key, value in schema.items()
                if key not in ("required", "title", "description", "type")
                and isinstance(value, dict)
            }
        required = schema.get("required", [])
        if not isinstance(required, list):
            required = []
        for name in required:
            if name not in args:
                violations.append(
                    Violation(
                        code="schema",
                        field=name,
                        message=f"Missing required parameter '{name}'",
                    )
                )
        additional_forbidden = schema.get("additionalProperties") is False
        for name, value in args.items():
            if properties and name not in properties:
                if additional_forbidden:
                    violations.append(
                        Violation(
                            code="schema",
                            field=name,
                            message=f"Unknown parameter '{name}' is not declared by the tool",
                        )
                    )
                continue
            expected = properties.get(name, {}).get("type")
            if isinstance(expected, list):
                actual = _JSON_TYPE_OF.get(type(value))
                if actual and actual not in expected:
                    violations.append(
                        Violation(
                            code="schema",
                            field=name,
                            message=f"Parameter '{name}' expects one of {expected}, got {actual}",
                        )
                    )
            elif isinstance(expected, str):
                actual = _JSON_TYPE_OF.get(type(value))
                if actual and actual != expected:
                    if not (expected == "number" and actual == "integer"):
                        violations.append(
                            Violation(
                                code="schema",
                                field=name,
                                message=f"Parameter '{name}' expects {expected}, got {actual}",
                            )
                        )
            enum = properties.get(name, {}).get("enum")
            if isinstance(enum, list) and value not in enum:
                violations.append(
                    Violation(
                        code="schema",
                        field=name,
                        message=f"Parameter '{name}' must be one of {enum}",
                    )
                )
        return violations

    def _pattern_violations(self, args: dict[str, Any]) -> list[Violation]:
        violations: list[Violation] = []
        material = {fragment for value in args.values() for fragment in _iter_strings(value)}
        for rule, pattern in self.rules.patterns():
            for fragment in sorted(material):
                if pattern.search(fragment):
                    violations.append(
                        Violation(
                            code="pattern",
                            rule=rule.id,
                            message=f"Argument matches denied pattern '{rule.id}'"
                            + (f" ({rule.description})" if rule.description else ""),
                        )
                    )
                    break
        return violations

    def _range_violations(self, args: dict[str, Any]) -> list[Violation]:
        violations: list[Violation] = []
        for name, value in args.items():
            limit = self.rules.numeric_limits.get(str(name).casefold())
            if limit is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if limit.min is not None and value < limit.min:
                violations.append(
                    Violation(
                        code="range",
                        field=name,
                        rule=f"min={limit.min}",
                        message=f"Parameter '{name}' below minimum {limit.min}",
                    )
                )
            if limit.max is not None and value > limit.max:
                violations.append(
                    Violation(
                        code="range",
                        field=name,
                        rule=f"max={limit.max}",
                        message=f"Parameter '{name}' above maximum {limit.max}",
                    )
                )
        return violations

    # -- middleware entry point ---------------------------------------------

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self.rules.mode == "off":
            return await handler(request)
        call = request.tool_call
        name = call["name"]
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        fields = {"tool": name, "tool_call_id": call["id"]}
        tool = getattr(request, "tool", None)

        if tool is None:
            # Not resolvable against the registry this middleware was built from.
            suggestions = difflib.get_close_matches(
                name, list(self.inventory), n=3, cutoff=self.rules.similarity_cutoff
            )
            violations = [
                Violation(
                    code="unknown_tool",
                    message="Unknown tool '"
                    + name
                    + "'"
                    + (f"; did you mean: {', '.join(suggestions)}?" if suggestions else ""),
                )
            ]
            self.context.emit(
                "tool.rejected",
                {**fields, "violations": [item.as_event() for item in violations]},
            )
            return _rejection(call, violations)

        violations = []
        schema = _tool_schema(tool)
        if schema:
            violations.extend(self._schema_violations(schema, args))
        violations.extend(self._pattern_violations(args))
        violations.extend(self._range_violations(args))
        if not violations:
            return await handler(request)

        self.context.emit(
            "tool.guardrail",
            {
                **fields,
                "mode": self.rules.mode,
                "violations": [item.as_event() for item in violations],
            },
        )
        if self.rules.mode == "fail":
            self.context.emit(
                "tool.rejected",
                {**fields, "violations": [item.as_event() for item in violations]},
            )
            return _rejection(call, violations)
        return await handler(request)


def _rejection(call: dict[str, Any], violations: list[Violation]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "error": "Tool call rejected by framework guardrails",
                "violations": [item.as_event() for item in violations],
            },
            ensure_ascii=False,
        ),
        tool_call_id=call["id"],
        status="error",
    )
