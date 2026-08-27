"""Load business-owned rule reference data."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RuleReferenceData(BaseModel):
    ppe_requirements: dict[str, list[str]] = Field(default_factory=dict)
    hot_work_ppe: list[str] = Field(default_factory=list)
    hot_work_risk_template: str = ""


def load_reference_data(path: str | Path | None = None) -> RuleReferenceData:
    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[3] / "config" / "doc_review_rules.yaml"
    )
    if not config_path.exists():
        return RuleReferenceData()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return RuleReferenceData.model_validate(raw)


__all__ = ["RuleReferenceData", "load_reference_data"]
