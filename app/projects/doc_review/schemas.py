"""Typed domain models for the document-review workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    CONSTRUCTION_PROGRAMME = "CONSTRUCTION_PROGRAMME"
    CONSTRUCTION_TECH_DISCLOSE = "CONSTRUCTION_TECH_DISCLOSE"
    HOT_WORK_DISCLOSE = "HOT_WORK_DISCLOSE"


class RuleSeverity(StrEnum):
    REMINDER = "REMINDER"
    REJECT = "REJECT"
    SUGGESTION = "SUGGESTION"


class RuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIABLE = "UNVERIFIABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewDecision(StrEnum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FAILED = "FAILED"


class DocumentSource(BaseModel):
    document_id: str
    document_type: DocumentType
    file_name: str
    file_key: str = ""
    file_type: str = ""
    file_size: int = 0
    download_url: str
    fallback_url: str | None = None


class OcrArtifact(BaseModel):
    document_id: str
    document_type: DocumentType
    file_name: str
    markdown: str = ""
    image_keys: list[str] = Field(default_factory=list)
    error: str | None = None


class Evidence(BaseModel):
    document_id: str | None = None
    page: int | None = None
    location: str = ""
    quote: str = ""


class RuleResult(BaseModel):
    rule_id: str
    rule_name: str
    severity: RuleSeverity
    status: RuleStatus
    document_type: DocumentType | None = None
    document_id: str | None = None
    issue: str = ""
    expected: str = ""
    actual: str = ""
    suggestion: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    source: str = "rule_engine"


class AgentReviewBundle(BaseModel):
    document_id: str
    document_type: DocumentType
    facts: dict[str, Any] = Field(default_factory=dict)
    results: list[RuleResult] = Field(default_factory=list)
    raw_output: str = ""


class ReviewReport(BaseModel):
    review_id: str
    status: str = "completed"
    decision: ReviewDecision
    summary: str
    total_files: int
    reject_count: int = 0
    suggestion_count: int = 0
    unverifiable_count: int = 0
    results: list[RuleResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReviewJobView(BaseModel):
    review_id: str
    status: ReviewJobStatus
    report: ReviewReport | None = None
    error: str | None = None


@dataclass
class ReviewContext:
    review_id: str
    raw_entity: dict[str, Any]
    documents: list[DocumentSource]
    artifacts: dict[str, OcrArtifact] = field(default_factory=dict)
    agent_reviews: list[AgentReviewBundle] = field(default_factory=list)
    rule_results: list[RuleResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class WorkMarkState(StrEnum):
    CHECKED = "CHECKED"
    UNCHECKED = "UNCHECKED"
    BLANK = "BLANK"
    UNKNOWN = "UNKNOWN"


class SectionFact(BaseModel):
    standard_title: str
    observed_title: str = ""
    content_present: bool | None = None
    content_equivalent: bool | None = None
    subheadings: list[str] = Field(default_factory=list)


class TableFact(BaseModel):
    name: str
    headers: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)


class PersonnelFact(BaseModel):
    name: str = ""
    age: int | None = None
    has_certificate: bool | None = None
    certificate_status: str = ""


class DailyRecordFact(BaseModel):
    date: str = ""
    activity: str = ""
    participants: str = ""
    precautions: str = ""


class EmergencyRoleFact(BaseModel):
    role: str = ""
    name: str = ""
    phone: str = ""


class SignatureFact(BaseModel):
    role: str = ""
    name: str = ""
    date: str = ""


class ProgrammeFacts(BaseModel):
    sections: list[SectionFact] = Field(default_factory=list)
    tables: list[TableFact] = Field(default_factory=list)
    proofreader_name: str = ""
    approver_name: str = ""
    supplier_name: str = ""
    seal_text: str = ""
    work_type_states: dict[str, WorkMarkState] = Field(default_factory=dict)
    personnel: list[PersonnelFact] = Field(default_factory=list)
    construction_start: str = ""
    construction_end: str = ""
    daily_records: list[DailyRecordFact] = Field(default_factory=list)
    specific_chemicals_registered: bool | None = None
    emergency_format: str = "UNKNOWN"
    emergency_roles: list[EmergencyRoleFact] = Field(default_factory=list)


class TechDisclosureFacts(BaseModel):
    risk_analysis: dict[str, str] = Field(default_factory=dict)
    ppe: dict[str, list[str]] = Field(default_factory=dict)
    emergency_measures: dict[str, str] = Field(default_factory=dict)
    work_type_states: dict[str, WorkMarkState] = Field(default_factory=dict)
    signatures: list[SignatureFact] = Field(default_factory=list)
    signature_section_present: bool | None = None
    signatures_complete: bool | None = None


class HotWorkFacts(BaseModel):
    risk_analysis: str = ""
    ppe: list[str] = Field(default_factory=list)
    signatures: list[SignatureFact] = Field(default_factory=list)
    signature_section_present: bool | None = None
    signatures_complete: bool | None = None


class TypoFact(BaseModel):
    location: str = ""
    original: str
    correction: str


class TypoReviewFacts(BaseModel):
    by_document: dict[str, list[TypoFact]] = Field(default_factory=dict)


class SealVisualFact(BaseModel):
    available: bool = False
    has_red_seal: bool | None = None
    seal_text: str = ""
    in_bottom: bool | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SignatureVisualFact(BaseModel):
    document_id: str
    role: str = ""
    handwritten: bool | None = None
    date_handwritten: bool | None = None
    name: str = ""
    date: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VisualReviewFacts(BaseModel):
    seal: SealVisualFact = Field(default_factory=SealVisualFact)
    signatures: list[SignatureVisualFact] = Field(default_factory=list)


__all__ = [
    "AgentReviewBundle",
    "DocumentSource",
    "DocumentType",
    "Evidence",
    "OcrArtifact",
    "ReviewContext",
    "ReviewDecision",
    "ReviewJobStatus",
    "ReviewJobView",
    "ReviewReport",
    "RuleResult",
    "RuleSeverity",
    "RuleStatus",
    "DailyRecordFact",
    "EmergencyRoleFact",
    "HotWorkFacts",
    "PersonnelFact",
    "ProgrammeFacts",
    "SealVisualFact",
    "SectionFact",
    "SignatureFact",
    "SignatureVisualFact",
    "TableFact",
    "TechDisclosureFacts",
    "TypoFact",
    "TypoReviewFacts",
    "VisualReviewFacts",
    "WorkMarkState",
]
