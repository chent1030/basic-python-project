"""Typed contracts for the construction-programme review workflow."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_SUGGESTIONS = "PASS_WITH_SUGGESTIONS"
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class FindingSeverity(StrEnum):
    REJECT = "REJECT"
    SUGGESTION = "SUGGESTION"
    REMINDER = "REMINDER"


class FindingStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class ReviewConfig(BaseModel):
    """Per-run provider and execution settings."""

    provider: str = "qwen"
    model: str = "qwen3.8"
    fallback_provider: str | None = None
    max_retries: int = Field(default=2, ge=0, le=5)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    retry_base_delay: float = Field(default=0.25, ge=0.0, le=30.0)


class VisualAsset(BaseModel):
    """An image passed to an extractor; ``asset_index`` is not a printed page."""

    asset_id: str
    document_id: str
    file_name: str
    asset_index: int = Field(ge=0)
    mime_type: str = "image/png"
    image_bytes: bytes = Field(exclude=True)
    sha256: str
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)


class BoundingBox(BaseModel):
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)


class VisualElement(BaseModel):
    type: str
    text: str = ""
    bbox: BoundingBox | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    asset_id: str
    elements: list[VisualElement] = Field(default_factory=list)
    raw_text: str = ""
    provider: str
    model: str
    attempt: int = Field(default=1, ge=1)
    error: str | None = None


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
    handwritten: bool | None = None
    date: str = ""


class SealFact(BaseModel):
    present: bool | None = None
    is_red: bool | None = None
    seal_text: str = ""
    color_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    color_method: str = ""


class ConstructionFacts(BaseModel):
    sections: list[SectionFact] = Field(default_factory=list)
    tables: list[TableFact] = Field(default_factory=list)
    proofreader_name: str = ""
    approver_name: str = ""
    seal: SealFact = Field(default_factory=SealFact)
    work_type_states: dict[str, str] = Field(default_factory=dict)
    personnel: list[PersonnelFact] = Field(default_factory=list)
    daily_records: list[DailyRecordFact] = Field(default_factory=list)
    construction_start: str = ""
    construction_end: str = ""
    specific_chemicals_registered: bool | None = None
    emergency_format: str = "UNKNOWN"
    emergency_subheadings: list[str] = Field(default_factory=list)
    emergency_roles: list[EmergencyRoleFact] = Field(default_factory=list)
    signatures: list[SignatureFact] = Field(default_factory=list)
    typos: list[dict[str, str]] = Field(default_factory=list)
    source_asset_ids: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    file_name: str
    asset_id: str
    asset_index: int
    bbox: BoundingBox | None = None
    observation_id: str = ""
    quote: str = ""


class RuleFinding(BaseModel):
    rule_id: str
    severity: FindingSeverity
    status: FindingStatus
    message: str = ""
    expected: str = ""
    actual: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    provider: str = "rule_engine"
    retryable: bool = False


class ProcessingStats(BaseModel):
    file_count: int = 0
    asset_count: int = 0
    failed_asset_count: int = 0
    retry_count: int = 0


class ReviewReport(BaseModel):
    run_id: str
    status: ReviewStatus
    summary: dict[str, int] = Field(default_factory=dict)
    findings: list[RuleFinding] = Field(default_factory=list)
    processing: ProcessingStats = Field(default_factory=ProcessingStats)
    created_at: datetime


__all__ = [
    "BoundingBox",
    "ConstructionFacts",
    "DailyRecordFact",
    "EmergencyRoleFact",
    "Evidence",
    "ExtractionResult",
    "FindingSeverity",
    "FindingStatus",
    "PersonnelFact",
    "ProcessingStats",
    "ReviewConfig",
    "ReviewReport",
    "ReviewStatus",
    "RuleFinding",
    "SealFact",
    "SectionFact",
    "SignatureFact",
    "TableFact",
    "VisualAsset",
    "VisualElement",
]
