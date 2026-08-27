from __future__ import annotations

from datetime import datetime

from app.projects.doc_review.catalog import RULES
from app.projects.doc_review.reference import RuleReferenceData
from app.projects.doc_review.rules import PROGRAMME_HEADINGS, WORK_TYPES, RuleEngine
from app.projects.doc_review.schemas import (
    DailyRecordFact,
    DocumentSource,
    DocumentType,
    EmergencyRoleFact,
    HotWorkFacts,
    PersonnelFact,
    ProgrammeFacts,
    ReviewDecision,
    RuleSeverity,
    RuleStatus,
    SealVisualFact,
    SectionFact,
    SignatureFact,
    SignatureVisualFact,
    TableFact,
    TechDisclosureFacts,
    TypoReviewFacts,
    VisualReviewFacts,
    WorkMarkState,
)


def _documents() -> list[DocumentSource]:
    return [
        DocumentSource(
            document_id="programme:1",
            document_type=DocumentType.CONSTRUCTION_PROGRAMME,
            file_name="programme.jpg",
            download_url="https://example.test/programme.jpg",
        ),
        DocumentSource(
            document_id="tech:1",
            document_type=DocumentType.CONSTRUCTION_TECH_DISCLOSE,
            file_name="tech.jpg",
            download_url="https://example.test/tech.jpg",
        ),
    ]


def _entity() -> dict:
    return {
        "vendorName": "江苏兆泰",
        "workDay": 7,
        "workInfo": [
            {"workType": "高处作业许可"},
            {"workType": "吊装作业许可"},
        ],
    }


def _programme() -> ProgrammeFacts:
    sections = [
        SectionFact(
            standard_title=title,
            observed_title=title,
            content_present=True,
            content_equivalent=True,
            subheadings=list(subheadings),
        )
        for title, subheadings in PROGRAMME_HEADINGS.items()
    ]
    tables = [
        TableFact(
            name="施工工具清单",
            headers=["工具", "数量"],
            rows=[{"工具": "扳手", "数量": "2"}],
        ),
        TableFact(
            name="现场作业人员信息",
            headers=["人数", "姓名", "工种", "持证情况", "年龄"],
        ),
        TableFact(
            name="风险评估",
            headers=[
                "活动",
                "识别的危险源",
                "风险评估",
                "风险等级",
                "风险处置措施",
                "采取措施后的风险评估",
                "处置后风险等级",
            ],
        ),
        TableFact(
            name="每天工作内容",
            headers=["日期", "活动", "参与人员", "注意事项"],
        ),
    ]
    states = {work_type: WorkMarkState.UNCHECKED for work_type in WORK_TYPES}
    states["高处作业"] = WorkMarkState.CHECKED
    states["吊装作业"] = WorkMarkState.CHECKED
    records = [
        DailyRecordFact(
            date=f"2026-08-{day:02d}",
            activity="设备检修",
            participants="张三、李四",
            precautions="佩戴防护用品",
        )
        for day in range(10, 17)
    ]
    return ProgrammeFacts(
        sections=sections,
        tables=tables,
        proofreader_name="王五",
        approver_name="赵六",
        supplier_name="江苏兆泰",
        seal_text="江苏兆泰",
        work_type_states=states,
        personnel=[
            PersonnelFact(
                name="张三",
                age=40,
                has_certificate=True,
                certificate_status="登高证",
            ),
            PersonnelFact(
                name="李四",
                age=42,
                has_certificate=False,
                certificate_status="无需持证",
            ),
            PersonnelFact(
                name="王五",
                age=45,
                has_certificate=False,
                certificate_status="无需持证",
            ),
        ],
        construction_start="2026-08-10",
        construction_end="2026-08-16",
        daily_records=records,
        specific_chemicals_registered=False,
        emergency_format="LINES",
        emergency_roles=[
            EmergencyRoleFact(role="组长", name="张三", phone="13800138000"),
            EmergencyRoleFact(role="安全员", name="李四", phone="13900139000"),
            EmergencyRoleFact(role="成员", name="王五", phone="13700137000"),
        ],
    )


def _tech() -> TechDisclosureFacts:
    states = {work_type: WorkMarkState.UNCHECKED for work_type in WORK_TYPES}
    states["高处作业"] = WorkMarkState.CHECKED
    states["吊装作业"] = WorkMarkState.CHECKED
    return TechDisclosureFacts(
        risk_analysis={
            "高处作业（吊顶夹层作业）": "存在坠落风险，设置防坠措施",
            "吊装作业": "存在起重伤害风险，设置警戒区",
        },
        ppe={
            "高处作业": ["安全带", "安全帽"],
            "吊装作业": ["安全帽", "反光背心"],
        },
        emergency_measures={
            "高处作业": "发生坠落立即救援并送医",
            "吊装作业": "停止吊装并隔离现场",
        },
        work_type_states=states,
        signatures=[
            SignatureFact(role="交底人", name="张三", date="2026-08-09"),
            SignatureFact(role="被交底人", name="李四", date="2026-08-09"),
        ],
        signature_section_present=True,
        signatures_complete=True,
    )


def _visual() -> VisualReviewFacts:
    return VisualReviewFacts(
        seal=SealVisualFact(
            available=True,
            has_red_seal=True,
            seal_text="江苏兆泰",
            in_bottom=True,
            confidence=0.99,
        ),
        signatures=[
            SignatureVisualFact(
                document_id=DocumentType.CONSTRUCTION_PROGRAMME.value,
                role="工程师",
                handwritten=True,
                date_handwritten=True,
                name="张三",
                date="8月9日",
                confidence=0.99,
            ),
            SignatureVisualFact(
                document_id=DocumentType.CONSTRUCTION_PROGRAMME.value,
                role="经理",
                handwritten=True,
                date_handwritten=True,
                name="李四",
                date="8月9日",
                confidence=0.99,
            ),
            SignatureVisualFact(
                document_id=DocumentType.CONSTRUCTION_TECH_DISCLOSE.value,
                role="交底人",
                handwritten=True,
                date_handwritten=True,
                name="张三",
                date="2026-08-09",
                confidence=0.99,
            ),
            SignatureVisualFact(
                document_id=DocumentType.CONSTRUCTION_TECH_DISCLOSE.value,
                role="被交底人",
                handwritten=True,
                date_handwritten=True,
                name="李四",
                date="2026-08-09",
                confidence=0.99,
            ),
        ],
    )


def _reference() -> RuleReferenceData:
    return RuleReferenceData(
        ppe_requirements={
            "高处作业": ["安全带", "安全帽"],
            "吊装作业": ["安全帽", "反光背心"],
        }
    )


def _result_by_id(report, rule_id: str):
    return next(item for item in report.results if item.rule_id == rule_id)


def test_all_catalog_rules_produce_one_result_and_pass_fixture() -> None:
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=_tech(),
        hot_work=None,
        typos=TypoReviewFacts(
            by_document={
                DocumentType.CONSTRUCTION_PROGRAMME.value: [],
                DocumentType.CONSTRUCTION_TECH_DISCLOSE.value: [],
            }
        ),
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert len(report.results) == len(RULES) == 35
    assert len({item.rule_id for item in report.results}) == 35
    assert report.decision == ReviewDecision.PASSED
    assert all(
        item.status in {RuleStatus.PASS, RuleStatus.NOT_APPLICABLE} for item in report.results
    )


def test_known_reject_failure_takes_precedence_over_unknowns() -> None:
    programme = _programme()
    programme.personnel[0].age = 56
    report = RuleEngine(RuleReferenceData()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=programme,
        tech=_tech(),
        hot_work=None,
        visual=VisualReviewFacts(),
    )

    assert _result_by_id(report, "P0-P1").status == RuleStatus.FAIL
    assert report.decision == ReviewDecision.REJECTED


def test_missing_evidence_is_manual_review_not_pass() -> None:
    report = RuleEngine(RuleReferenceData()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=None,
        tech=None,
        hot_work=None,
    )

    assert report.decision == ReviewDecision.MANUAL_REVIEW
    reject_results = [item for item in report.results if item.severity == RuleSeverity.REJECT]
    assert reject_results
    assert all(item.status == RuleStatus.UNVERIFIABLE for item in reject_results)


def test_hot_work_checked_without_document_is_rejected() -> None:
    programme = _programme()
    programme.work_type_states["动火作业"] = WorkMarkState.CHECKED
    entity = _entity()
    entity["workInfo"].append({"workType": "动火作业许可"})
    report = RuleEngine(_reference()).evaluate(
        entity=entity,
        documents=_documents(),
        programme=programme,
        tech=_tech(),
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert _result_by_id(report, "P0-F5").status == RuleStatus.FAIL
    assert report.decision == ReviewDecision.REJECTED


def test_programme_signature_requires_handwritten_valid_month_and_day() -> None:
    visual = _visual()
    for item in visual.signatures:
        if item.document_id == DocumentType.CONSTRUCTION_PROGRAMME.value:
            item.date = "2026-08"
            item.date_handwritten = False
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=_tech(),
        hot_work=None,
        typos=TypoReviewFacts(
            by_document={
                DocumentType.CONSTRUCTION_PROGRAMME.value: [],
                DocumentType.CONSTRUCTION_TECH_DISCLOSE.value: [],
            }
        ),
        visual=visual,
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    result = _result_by_id(report, "P0-F3")
    assert result.status == RuleStatus.FAIL
    assert "日期不是手写" in result.issue
    assert "有效月日" in result.issue


def test_complete_signature_extraction_rejects_missing_role() -> None:
    tech = _tech()
    tech.signatures = tech.signatures[:1]
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=tech,
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert _result_by_id(report, "P1-S1").status == RuleStatus.FAIL
    assert _result_by_id(report, "P1-S2").status == RuleStatus.FAIL


def test_incomplete_signature_extraction_requires_manual_review() -> None:
    tech = _tech()
    tech.signatures_complete = None
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=tech,
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert _result_by_id(report, "P1-S1").status == RuleStatus.UNVERIFIABLE
    assert _result_by_id(report, "P1-S3").status == RuleStatus.UNVERIFIABLE


def test_signature_date_outside_range_is_suggestion_only() -> None:
    tech = _tech()
    for signature in tech.signatures:
        signature.date = "2026-08-01"
    visual = _visual()
    for item in visual.signatures:
        if item.document_id == DocumentType.CONSTRUCTION_TECH_DISCLOSE.value:
            item.date = "2026-08-01"
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=tech,
        hot_work=None,
        typos=TypoReviewFacts(
            by_document={
                DocumentType.CONSTRUCTION_PROGRAMME.value: [],
                DocumentType.CONSTRUCTION_TECH_DISCLOSE.value: [],
            }
        ),
        visual=visual,
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    result = _result_by_id(report, "P1-S3")
    assert result.status == RuleStatus.FAIL
    assert result.severity == RuleSeverity.SUGGESTION
    assert report.decision == ReviewDecision.PASSED


def test_unknown_and_blank_work_marks_have_distinct_results() -> None:
    tech = _tech()
    tech.work_type_states["断路作业"] = WorkMarkState.UNKNOWN
    unknown_report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=tech,
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )
    assert _result_by_id(unknown_report, "P1-CK").status == RuleStatus.UNVERIFIABLE

    tech.work_type_states["断路作业"] = WorkMarkState.BLANK
    blank_report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=tech,
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )
    assert _result_by_id(blank_report, "P1-CK").status == RuleStatus.FAIL


def test_unknown_hot_work_mark_is_not_not_applicable() -> None:
    programme = _programme()
    programme.work_type_states["动火作业"] = WorkMarkState.UNKNOWN
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=programme,
        tech=_tech(),
        hot_work=HotWorkFacts(),
        visual=_visual(),
    )

    assert _result_by_id(report, "P0-F5").status == RuleStatus.UNVERIFIABLE
    assert all(
        _result_by_id(report, rule_id).status == RuleStatus.UNVERIFIABLE
        for rule_id in ("P2-TYPO", "P2-R1", "P2-PP", "P2-S1", "P2-S2", "P2-S3")
    )


def test_partial_seal_visual_evidence_is_unverifiable() -> None:
    visual = _visual()
    visual.seal.has_red_seal = None
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=_tech(),
        hot_work=None,
        visual=visual,
    )
    assert _result_by_id(report, "P0-F2").status == RuleStatus.UNVERIFIABLE


def test_low_confidence_visual_evidence_cannot_pass() -> None:
    visual = _visual()
    visual.seal.confidence = 0.2
    for item in visual.signatures:
        item.confidence = 0.2
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=_programme(),
        tech=_tech(),
        hot_work=None,
        visual=visual,
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert _result_by_id(report, "P0-F2").status == RuleStatus.UNVERIFIABLE
    assert _result_by_id(report, "P0-F3").status == RuleStatus.UNVERIFIABLE
    assert _result_by_id(report, "P1-S1").status == RuleStatus.UNVERIFIABLE


def test_unknown_programme_work_type_propagates_to_cross_document_rules() -> None:
    programme = _programme()
    programme.work_type_states["临时用电作业"] = WorkMarkState.UNKNOWN
    tech = _tech()
    tech.risk_analysis["临时用电作业"] = "临时用电风险"
    tech.emergency_measures["临时用电作业"] = "断电并隔离"
    report = RuleEngine(_reference()).evaluate(
        entity=_entity(),
        documents=_documents(),
        programme=programme,
        tech=tech,
        hot_work=None,
        visual=_visual(),
        initiation_time=datetime(2026, 8, 10, 10, 0),
    )

    assert _result_by_id(report, "P1-R2").status == RuleStatus.UNVERIFIABLE
    assert _result_by_id(report, "P1-EM").status == RuleStatus.UNVERIFIABLE
    assert _result_by_id(report, "P1-CK").status == RuleStatus.UNVERIFIABLE
