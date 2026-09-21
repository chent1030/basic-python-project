from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import AgentDefinition, Approval, DeepAgent, callable_version, segment


@dataclass(frozen=True)
class Input:
    path: str = ""


@dataclass(frozen=True)
class Output:
    step: str
    path: str = ""


@dataclass(frozen=True)
class Step:
    id: str
    agent: AgentDefinition | DeepAgent
    inputs: Any = field(default_factory=Input)
    approval: Approval | None = None
    alternatives: tuple[AgentDefinition | DeepAgent, ...] = ()

    def __post_init__(self) -> None:
        segment(self.id)
        if isinstance(self.agent, DeepAgent):
            object.__setattr__(self, "agent", self.agent.definition())
        object.__setattr__(
            self,
            "alternatives",
            tuple(
                item.definition() if isinstance(item, DeepAgent) else item
                for item in self.alternatives
            ),
        )


@dataclass(frozen=True, init=False)
class Sequence:
    children: tuple[Any, ...]
    approval: Approval | None

    def __init__(self, *children: Any, approval: Approval | None = None):
        object.__setattr__(self, "children", children)
        object.__setattr__(self, "approval", approval)


@dataclass(frozen=True, init=False)
class Parallel:
    children: tuple[Any, ...]
    max_concurrency: int
    failure_policy: Literal["require_all", "allow_partial"]
    approval: Approval | None

    def __init__(
        self,
        *children: Any,
        max_concurrency: int = 4,
        failure_policy: Literal["require_all", "allow_partial"] = "require_all",
        approval: Approval | None = None,
    ):
        if max_concurrency < 1 or failure_policy not in ("require_all", "allow_partial"):
            raise ValueError("Invalid parallel policy")
        object.__setattr__(self, "children", children)
        object.__setattr__(self, "max_concurrency", max_concurrency)
        object.__setattr__(self, "failure_policy", failure_policy)
        object.__setattr__(self, "approval", approval)


@dataclass(frozen=True)
class Condition:
    id: str
    predicate: Any
    when_true: Any
    when_false: Any


@dataclass(frozen=True)
class Loop:
    id: str
    body: Any
    until: Any
    max_iterations: int = 3

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("Loop requires an iteration budget")


@dataclass(frozen=True)
class Supervisor:
    id: str
    coordinator: AgentDefinition | DeepAgent
    members: tuple[AgentDefinition | DeepAgent, ...]
    approval: Approval = field(default_factory=Approval.none)
    max_rounds: int = 10
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.max_rounds < 1 or self.max_concurrency < 1:
            raise ValueError("Supervisor requires finite positive budgets")
        if isinstance(self.coordinator, DeepAgent):
            object.__setattr__(self, "coordinator", self.coordinator.definition())
        object.__setattr__(
            self,
            "members",
            tuple(
                member.definition() if isinstance(member, DeepAgent) else member
                for member in self.members
            ),
        )


def describe(node: Any) -> Any:
    if isinstance(node, Step):
        return {
            "kind": "step",
            "id": node.id,
            "agent": node.agent.snapshot(),
            "inputs": repr(node.inputs),
            "approval": repr(node.approval),
            "alternatives": [item.snapshot() for item in node.alternatives],
        }
    if isinstance(node, (Sequence, Parallel)):
        return {
            "kind": type(node).__name__,
            "children": [describe(c) for c in node.children],
            "concurrency": getattr(node, "max_concurrency", None),
            "approval": repr(node.approval),
            "failure_policy": getattr(node, "failure_policy", None),
        }
    if isinstance(node, Condition):
        return {
            "kind": "condition",
            "id": node.id,
            "true": describe(node.when_true),
            "false": describe(node.when_false),
            "predicate": callable_version(node.predicate),
        }
    if isinstance(node, Loop):
        return {
            "kind": "loop",
            "id": node.id,
            "body": describe(node.body),
            "max_iterations": node.max_iterations,
            "until": callable_version(node.until),
        }
    if isinstance(node, Supervisor):
        return {
            "kind": "supervisor",
            "id": node.id,
            "coordinator": node.coordinator.snapshot(),
            "members": [member.snapshot() for member in node.members],
            "approval": repr(node.approval),
            "max_rounds": node.max_rounds,
        }
    raise TypeError(f"Unsupported composition: {type(node)}")


def agents_in(node: Any) -> list[AgentDefinition]:
    if isinstance(node, Step):
        return [node.agent, *node.alternatives]
    if isinstance(node, (Sequence, Parallel)):
        return [agent for child in node.children for agent in agents_in(child)]
    if isinstance(node, Condition):
        return agents_in(node.when_true) + agents_in(node.when_false)
    if isinstance(node, Loop):
        return agents_in(node.body)
    if isinstance(node, Supervisor):
        return [node.coordinator, *node.members]
    raise TypeError(type(node))


def validate_composition(node: Any, available: frozenset[str] = frozenset()) -> set[str]:
    def check_binding(value: Any) -> None:
        if isinstance(value, Output) and value.step not in available:
            raise ValueError(f"Output reference {value.step} is not available before this step")
        if isinstance(value, dict):
            for item in value.values():
                check_binding(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                check_binding(item)

    if isinstance(node, Step):
        check_binding(node.inputs)
        return {node.id}
    if isinstance(node, (Sequence, Parallel)):
        exported: set[str] = set()
        for child in node.children:
            incoming = available | frozenset(exported) if isinstance(node, Sequence) else available
            names = validate_composition(child, incoming)
            if names & exported:
                raise ValueError(f"Duplicate output step IDs: {sorted(names & exported)}")
            exported.update(names)
        return exported
    if isinstance(node, Condition):
        validate_composition(node.when_true, available)
        validate_composition(node.when_false, available)
        return {node.id}
    if isinstance(node, Loop):
        validate_composition(node.body, available)
        return {node.id}
    if isinstance(node, Supervisor):
        if len({member.name for member in node.members}) != len(node.members):
            raise ValueError("Supervisor member names must be unique")
        if node.coordinator.name in {member.name for member in node.members}:
            raise ValueError("Supervisor cannot delegate to itself")
        return {node.id}
    raise TypeError(type(node))
