from __future__ import annotations

from app.projects.construction_plan_review.models import (
    ConstructionFacts,
    FindingStatus,
    PersonnelFact,
    SectionFact,
    SignatureFact,
    TableFact,
)
from app.projects.construction_plan_review.rules import ConstructionPlanRuleEngine


def _finding(facts: ConstructionFacts, rule_id: str):
    findings = ConstructionPlanRuleEngine().evaluate(entity={"workDay": 0}, facts=facts)
    return next(item for item in findings if item.rule_id == rule_id)


def test_zero_quantity_tool_is_suggestion() -> None:
    facts = ConstructionFacts(
        tables=[
            TableFact(
                name="施工工具清单",
                headers=["工具", "数量"],
                rows=[{"工具": "电钻", "数量": "0"}],
            )
        ]
    )
    finding = _finding(facts, "CP-H1")
    assert finding.status == FindingStatus.FAIL
    assert finding.severity == "SUGGESTION"
    assert "电钻" in finding.actual


def test_renamed_equivalent_heading_is_suggestion() -> None:
    facts = ConstructionFacts(
        sections=[
            SectionFact(
                standard_title="施工计划",
                observed_title="施工安排",
                content_equivalent=True,
            )
        ]
    )
    finding = _finding(facts, "CP-C2")
    assert finding.status == FindingStatus.FAIL


def test_uncertified_person_must_use_required_text() -> None:
    facts = ConstructionFacts(
        personnel=[
            PersonnelFact(
                name="张三",
                age=30,
                has_certificate=False,
                certificate_status="无",
            )
        ]
    )
    finding = _finding(facts, "CP-P2")
    assert finding.status == FindingStatus.FAIL


def test_signature_date_needs_at_least_month_and_day() -> None:
    facts = ConstructionFacts(
        signatures=[
            SignatureFact(role="工程师", handwritten=True, date="8"),
            SignatureFact(role="经理", handwritten=True, date="8月10日"),
        ]
    )
    finding = _finding(facts, "CP-F3")
    assert finding.status == FindingStatus.REVIEW_REQUIRED
