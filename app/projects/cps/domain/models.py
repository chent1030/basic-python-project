from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import Contract

SPECIALISTS = (
    "issue_identification",
    "rectification_judgement",
    "history_analysis",
    "coverage_analysis",
    "report",
    "work_plan",
)
RESULT_KEYS = {
    "issue_identification": "issues",
    "rectification_judgement": "rectification",
    "history_analysis": "history_analysis",
    "coverage_analysis": "coverage",
    "report": "report",
    "work_plan": "work_plan",
}


class LineInfo(Contract):
    line_id: str = Field(min_length=1, max_length=100)
    area: str = Field(min_length=1, max_length=100)
    supervisor_id: str = Field(min_length=1, max_length=100)
    modifications: str = Field(default="", max_length=20000)
    expected_scenarios: list[str] = Field(default_factory=list, max_length=100)


class CreateInspection(Contract):
    goal: str = Field(min_length=1, max_length=10000)
    line_info: LineInfo
    required_outputs: list[
        Literal["issues", "rectification", "history_analysis", "coverage", "report", "work_plan"]
    ] = Field(default_factory=lambda: ["issues", "report", "work_plan"], max_length=6)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AmendInspection(Contract):
    expected_version: int = Field(ge=1)
    goal: str | None = Field(default=None, min_length=1, max_length=10000)
    line_info: LineInfo | None = None
    note: str = Field(default="", max_length=20000)


class VersionedCommand(Contract):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class StopInspection(VersionedCommand):
    action: Literal["return", "cancel"]
    reason: str = Field(min_length=1, max_length=5000)


class DispatchReview(VersionedCommand):
    action: Literal["approve", "modify", "skip", "retry", "return", "manual"]
    selected_agent: str | None = None
    reason: str = Field(min_length=1, max_length=5000)
    instruction: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def check_selection(self):
        if self.selected_agent is not None and self.selected_agent not in SPECIALISTS:
            raise ValueError("Agent is not an allowed CPS specialist")
        if self.action == "modify" and self.selected_agent is None:
            raise ValueError("A replacement agent is required")
        if self.action in ("skip", "return", "manual") and self.selected_agent is not None:
            raise ValueError("This action cannot dispatch an agent")
        return self


class ResultReview(VersionedCommand):
    accept: bool = True
    edited: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=5000)


class ManualResult(VersionedCommand):
    agent: Literal[
        "issue_identification",
        "rectification_judgement",
        "history_analysis",
        "coverage_analysis",
        "report",
        "work_plan",
    ]
    output: dict[str, Any]
    reason: str = Field(min_length=1, max_length=5000)


class EvidenceInput(VersionedCommand):
    kind: Literal["before", "after", "context"]
    issue_id: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=5000)
    content_base64: str = Field(min_length=4, max_length=14_000_000)


class MemoryReview(Contract):
    expected_version: int = Field(ge=1)
    action: Literal["accept", "reject", "disable", "enable", "rollback"]
    reason: str = Field(min_length=1, max_length=5000)
    rollback_version: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    scope: str | None = Field(default=None, min_length=1, max_length=100)
    knowledge: str | None = Field(default=None, min_length=1, max_length=10000)


class TaskUpdate(VersionedCommand):
    status: Literal["in_progress", "completed", "cancelled"]
    note: str = Field(min_length=1, max_length=5000)


class AgentInput(Contract):
    inspection_id: str
    job_id: str


class Inspection(Contract):
    id: str
    owner: str
    goal: str
    line_info: LineInfo
    required_outputs: list[str]
    status: Literal[
        "collecting", "active", "needs_input", "needs_human", "completed", "cancelled"
    ] = "collecting"
    version: int = 1
    evidence_version: int = 1
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)
    confirmations: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[dict[str, Any]] = Field(default_factory=list)
    active_job: str | None = None
    jobs: list[str] = Field(default_factory=list)
    rounds: int = 0
    archived_report: str | None = None
    published_plan: str | None = None
    created_at: float
    updated_at: float
    completed_at: float | None = None

    def can_finish(self) -> bool:
        if any(key not in self.confirmations for key in self.required_outputs):
            return False
        if "report" in self.required_outputs and not self.archived_report:
            return False
        return "work_plan" not in self.required_outputs or self.published_plan is not None

    def invalidate(self) -> None:
        self.results.clear()
        self.confirmations.clear()
        self.archived_report = None
        self.published_plan = None
        self.evidence_version += 1
