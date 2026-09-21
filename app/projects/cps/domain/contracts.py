from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Confidence = Annotated[float, Field(ge=0, le=1)] | None
Severity = Literal["low", "medium", "high", "unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sourced(Contract):
    source_refs: list[str] = Field(min_length=1)


class ProposalOutput(Contract):
    action: Literal["propose_agent", "request_input", "request_human", "finish"]
    agent_name: str | None
    reason: str = Field(min_length=1)
    expected_output: str
    prerequisites: list[str]
    missing_inputs: list[str]
    source_refs: list[str]
    requires_human_confirmation: Literal[True]

    @model_validator(mode="after")
    def validate_action(self):
        if (self.action == "propose_agent") != (self.agent_name is not None):
            raise ValueError("only propose_agent can specify an agent")
        if self.action == "request_input" and not self.missing_inputs:
            raise ValueError("request_input must identify missing inputs")
        return self


class ResultOutput(Contract):
    status: Literal["completed", "needs_input", "needs_human", "failed"]
    summary: str = Field(min_length=1)
    missing_inputs: list[str]
    warnings: list[str]
    human_confirmation_required: Literal[True]

    @model_validator(mode="after")
    def validate_missing_inputs(self):
        if self.status == "needs_input" and not self.missing_inputs:
            raise ValueError("needs_input must identify missing inputs")
        return self


class Issue(Sourced):
    issue_id: str
    category: str
    description: str
    location: str | None
    severity: Severity
    suggested_action: str
    confidence: Confidence
    limitations: list[str]


class IssueOutput(ResultOutput):
    issues: list[Issue]


class RectificationCheck(Sourced):
    issue_id: str
    criterion: str
    verdict: Literal["satisfied", "unsatisfied", "unknown"]


class Rectification(Contract):
    status: Literal["pass", "partial", "fail", "unable_to_judge"]
    checks: list[RectificationCheck]
    remaining_items: list[str]
    next_action: str
    confidence: Confidence

    @model_validator(mode="after")
    def validate_pass(self):
        if self.status == "pass" and (
            not self.checks
            or self.remaining_items
            or any(check.verdict != "satisfied" for check in self.checks)
        ):
            raise ValueError("pass requires satisfied checks and no remaining items")
        return self


class RectificationOutput(ResultOutput):
    rectification: Rectification


class Metric(Sourced):
    name: str
    value: float | None
    unit: str
    denominator: float | None


class History(Contract):
    period: str | None
    scope: str
    metrics: list[Metric]
    findings: list[str]
    limitations: list[str]


class HistoryOutput(ResultOutput):
    history_analysis: History


class Gap(Sourced):
    scope: str
    kind: Literal["confirmed_gap", "insufficient_evidence", "hypothesis"]
    description: str
    priority: Severity


class Coverage(Contract):
    baseline_ref: str | None
    gaps: list[Gap]
    recommendations: list[str]


class CoverageOutput(ResultOutput):
    coverage: Coverage


class ReportSection(Sourced):
    title: str
    content: str
    claim_type: Literal["fact", "inference", "human_confirmed", "recommendation"]


class Report(Contract):
    title: str
    summary: str
    risk: Severity
    sections: list[ReportSection]
    review_items: list[str]
    draft: Literal[True]


class ReportOutput(ResultOutput):
    report: Report


class MemoryCandidate(Sourced):
    confidence: Confidence = None
    kind: Literal["episodic", "procedural"]
    knowledge: str
    scope: str
    condition: str
    human_reason: str | None
    outcome: str
    limitations: list[str]
    evaluation: str
    review_status: Literal["pending"]


class ObservationOutput(ResultOutput):
    memory_candidates: list[MemoryCandidate]


class WorkTask(Sourced):
    task_id: str
    objective: str
    scope: str
    priority: Severity
    owner_id: str | None
    due_at: str | None
    acceptance_criteria: list[str]
    dependencies: list[str]
    draft: Literal[True]


class WorkPlanOutput(ResultOutput):
    work_plan: list[WorkTask]


CONTRACTS: dict[str, type[Contract]] = {
    "main": ProposalOutput,
    "issue_identification": IssueOutput,
    "rectification_judgement": RectificationOutput,
    "history_analysis": HistoryOutput,
    "coverage_analysis": CoverageOutput,
    "report": ReportOutput,
    "observation": ObservationOutput,
    "work_plan": WorkPlanOutput,
}
