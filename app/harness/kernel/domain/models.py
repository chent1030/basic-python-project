from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def identity() -> str:
    return uuid4().hex


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def segment(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise ValueError(f"Invalid identity: {value!r}")
    return value


def callable_version(value: Any) -> str | None:
    if value is None:
        return None
    target = value if inspect.isfunction(value) or inspect.isclass(value) else type(value)
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        source = target.__qualname__
    return digest([target.__module__, target.__qualname__, source])


class FrameworkError(Exception):
    pass


class Conflict(FrameworkError):
    pass


class Forbidden(FrameworkError):
    pass


class Waiting(FrameworkError):
    pass


class Cancelled(FrameworkError):
    pass


class Uncertain(FrameworkError):
    pass


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    task_id: str
    run_id: str
    invocation_id: str = "root"
    attempt_id: str = "initial"

    def __post_init__(self) -> None:
        for value in vars(self).values():
            segment(value)

    def child(self, invocation_id: str, attempt_id: str) -> Scope:
        return Scope(self.tenant_id, self.task_id, self.run_id, invocation_id, attempt_id)


@dataclass(frozen=True)
class Approval:
    mode: Literal["none", "before", "after", "delegation", "inherit"] = "none"
    roles: tuple[str, ...] = ("approver",)
    ttl_seconds: int = 86400
    condition: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in {"none", "before", "after", "delegation", "inherit"}:
            raise ValueError("Unknown approval mode")
        if self.ttl_seconds <= 0 or not self.roles:
            raise ValueError("Approval requires roles and positive expiry")

    @classmethod
    def none(cls) -> Approval:
        return cls()

    @classmethod
    def before(cls, **kwargs: Any) -> Approval:
        return cls(mode="before", **kwargs)

    @classmethod
    def after(cls, **kwargs: Any) -> Approval:
        return cls(mode="after", **kwargs)

    @classmethod
    def every_delegation(cls, **kwargs: Any) -> Approval:
        return cls(mode="delegation", **kwargs)


@dataclass(frozen=True)
class Retry:
    attempts: int = 1
    delay_seconds: float = 0.5
    idempotent: bool = False
    exceptions: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)

    def __post_init__(self) -> None:
        if self.attempts < 1 or self.delay_seconds < 0:
            raise ValueError("Invalid retry limits")
        if self.attempts > 1 and not self.idempotent:
            raise ValueError("Business retries require an explicit idempotency contract")


class Dispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    inputs: dict[str, Any]
    reason: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delegate", "finish"]
    calls: list[Dispatch] = Field(default_factory=list, max_length=16)
    output: Any = None


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    version: str = "1.0.0"
    model_profile: str = "default"
    system_prompt: str = ""
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None
    tools: tuple[Any, ...] = ()
    toolkits: tuple[Any, ...] = ()
    skills: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    middleware: tuple[Any, ...] = ()
    subagents: tuple[Any, ...] = ()
    delegates: tuple[str, ...] = ()
    interrupt_on: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[Any, ...] = ()
    approval: Approval = field(default_factory=Approval.none)
    retry: Retry = field(default_factory=Retry)
    timeout_seconds: float = 300
    recursion_limit: int = 100
    max_model_calls: int = 40
    max_tool_calls: int = 100
    planning: bool = True
    general_purpose: bool = False
    state_schema: Any = None
    context_schema: Any = None
    context_factory: Any = None
    initial_state_factory: Any = None
    backend_factory: Any = None
    cache_factory: Any = None
    handler: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        segment(self.name)
        if (
            min(
                self.timeout_seconds,
                self.recursion_limit,
                self.max_model_calls,
                self.max_tool_calls,
            )
            <= 0
        ):
            raise ValueError("Agent budgets must be positive")
        if self.handler is None and not self.system_prompt.strip():
            raise ValueError("LLM agents require an explicit role/boundary prompt")

    def validate_input(self, value: Any) -> Any:
        return (
            self.input_schema.model_validate(value).model_dump(mode="json")
            if self.input_schema
            else value
        )

    def validate_output(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return (
            self.output_schema.model_validate(value).model_dump(mode="json")
            if self.output_schema
            else value
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "model_profile": self.model_profile,
            "prompt_hash": digest(self.system_prompt),
            "tools": [
                getattr(tool, "name", getattr(tool, "__name__", type(tool).__name__))
                for tool in self.tools
            ],
            "toolkits": [item.snapshot() for item in self.toolkits],
            "skills": list(self.skills),
            "memory": list(self.memory),
            "middleware": [type(item).__qualname__ for item in self.middleware],
            "subagents": [
                {
                    "definition": item.definition.snapshot(),
                    "mode": item.mode,
                    "description": item.description,
                    "input_mapper": callable_version(item.input_mapper),
                }
                for item in self.subagents
            ],
            "delegates": list(self.delegates),
            "handler": callable_version(self.handler),
            "interrupt_on": self.interrupt_on,
            "approval": self.approval.mode,
            "planning": self.planning,
            "general_purpose": self.general_purpose,
            "input_schema": self.input_schema.model_json_schema() if self.input_schema else None,
            "output_schema": self.output_schema.model_json_schema() if self.output_schema else None,
            "timeout": self.timeout_seconds,
            "recursion_limit": self.recursion_limit,
            "backend": callable_version(self.backend_factory),
            "backend_configuration": self.backend_factory.snapshot()
            if hasattr(self.backend_factory, "snapshot")
            else None,
            "cache": callable_version(self.cache_factory),
            "cache_configuration": self.cache_factory.snapshot()
            if hasattr(self.cache_factory, "snapshot")
            else None,
            "handler_configuration": self.handler.snapshot()
            if hasattr(self.handler, "snapshot")
            else None,
            "context_schema": callable_version(self.context_schema),
            "context_factory": callable_version(self.context_factory),
            "initial_state_factory": callable_version(self.initial_state_factory),
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "retry": {
                "attempts": self.retry.attempts,
                "delay": self.retry.delay_seconds,
                "idempotent": self.retry.idempotent,
            },
        }


class DeepAgent:
    name = "agent"
    version = "1.0.0"
    model_profile = "default"
    system_prompt = ""
    prompt_file: str | None = None

    def definition(self) -> AgentDefinition:
        from pathlib import Path

        fields = AgentDefinition.__dataclass_fields__
        values = {name: getattr(self, name) for name in fields if hasattr(self, name)}
        if self.prompt_file:
            values["system_prompt"] = Path(self.prompt_file).read_text(encoding="utf-8")
        return AgentDefinition(**values)
