"""Deterministic P0/P1/P2 decision engine."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from app.projects.doc_review.catalog import RULE_BY_ID, RULES, RuleSpec
from app.projects.doc_review.reference import RuleReferenceData
from app.projects.doc_review.schemas import (
    DocumentSource,
    DocumentType,
    HotWorkFacts,
    ProgrammeFacts,
    ReviewDecision,
    ReviewReport,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    SignatureFact,
    SignatureVisualFact,
    TableFact,
    TechDisclosureFacts,
    TypoReviewFacts,
    VisualReviewFacts,
    WorkMarkState,
)

PROGRAMME_HEADINGS: dict[str, tuple[str, ...]] = {
    "工程概况及有关参数": ("详细的施工地点", "施工工具", "施工涉及区域", "详细施工内容"),
    "现场作业人员信息": (),
    "施工计划": ("施工前准备工作", "详细施工流程"),
    "风险识别": (),
    "风险评估取值指引": (),
    "安全保证措施": (),
    "每天的工作内容详细描述": (),
    "化学品管理": (),
    "应急救援预案": ("应急组织架构", "各成员职责和联系电话", "各项风险事故的应急处置流程"),
    "环境保护及文明施工措施": (),
}

WORK_TYPES = (
    "动火作业",
    "高处作业",
    "有限空间作业",
    "盲板抽堵作业",
    "吊装作业",
    "消防中断作业",
    "动土作业",
    "临时用电作业",
    "断路作业",
    "砸墙穿楼板作业",
)

WORK_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "动火作业": ("动火作业", "动火作业许可"),
    "高处作业": ("高处作业", "高处作业许可", "高处作业（吊顶夹层作业）", "吊顶夹层作业"),
    "有限空间作业": ("有限空间作业", "有限空间作业许可"),
    "盲板抽堵作业": ("盲板抽堵作业", "盲板抽堵作业（危险管道作业）", "危险管道作业"),
    "吊装作业": ("吊装作业", "吊装作业许可"),
    "消防中断作业": ("消防中断作业", "消防中断作业许可"),
    "动土作业": ("动土作业", "动土作业许可"),
    "临时用电作业": ("临时用电作业", "临时用电作业许可"),
    "断路作业": ("断路作业", "断路作业许可"),
    "砸墙穿楼板作业": ("砸墙穿楼板作业", "砸墙&穿楼板作业"),
}

ALLOWED_CERTIFICATES = ("电工证", "焊工证", "焊接证", "登高证", "安全员证")
MIN_VISUAL_CONFIDENCE = 0.7

TABLE_RULES: dict[str, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]] = {
    "P0-H1": (("工具", "施工工具"), (("工具",), ("数量",))),
    "P0-H2": (
        ("作业人员", "人员信息"),
        (("人数", "人员数量"), ("姓名",), ("工种",), ("持证情况", "证书"), ("年龄",)),
    ),
    "P0-H3": (
        ("风险评估",),
        (
            ("活动",),
            ("危险源",),
            ("风险评估",),
            ("风险等级",),
            ("处置措施", "风险处置措施"),
            ("采取措施后的风险评估", "措施后风险评估"),
            ("处置后风险等级", "处置后等级"),
        ),
    ),
    "P0-H4": (("每天", "每日工作"), (("日期",), ("活动",), ("参与人员",), ("注意事项",))),
    "P0-H5": (
        ("化学品",),
        (("化学品名称", "名称"), ("数量",), ("存储规定", "储存规定"), ("危险", "危害")),
    ),
}


def normalize_work_type(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value or "")
    if compact in {"普通作业类别", "普通作业"}:
        return None
    for standard, aliases in WORK_TYPE_ALIASES.items():
        if any(re.sub(r"\s+", "", alias) == compact for alias in aliases):
            return standard
    return compact or None


def _spec(rule_id: str) -> RuleSpec:
    return RULE_BY_ID[rule_id]


def _result(
    rule_id: str,
    status: RuleStatus,
    *,
    issue: str = "",
    actual: str = "",
    suggestion: str = "",
    confidence: float = 1.0,
) -> RuleResult:
    spec = _spec(rule_id)
    return RuleResult(
        rule_id=rule_id,
        rule_name=spec.name,
        severity=spec.severity,
        status=status,
        document_type=spec.document_type,
        issue=issue,
        expected=spec.requirement,
        actual=actual,
        suggestion=suggestion,
        confidence=confidence,
    )


def _unverifiable(rule_id: str, reason: str) -> RuleResult:
    return _result(rule_id, RuleStatus.UNVERIFIABLE, issue=reason, confidence=0.0)


def _compact(value: str) -> str:
    return re.sub(r"[\s:：、，,；;（）()\-_/]", "", value or "")


def _contains_alias(value: str, aliases: tuple[str, ...]) -> bool:
    compact = _compact(value)
    return any(
        _compact(alias) in compact or compact in _compact(alias) for alias in aliases if compact
    )


def _find_table(tables: list[TableFact], aliases: tuple[str, ...]) -> TableFact | None:
    for table in tables:
        if _contains_alias(table.name, aliases):
            return table
    return None


def _missing_headers(table: TableFact, groups: tuple[tuple[str, ...], ...]) -> list[str]:
    joined = "|".join(table.headers)
    return ["/".join(group) for group in groups if not _contains_alias(joined, group)]


def _checked_types(programme: ProgrammeFacts) -> set[str]:
    return {
        standard
        for raw, state in programme.work_type_states.items()
        if state == WorkMarkState.CHECKED and (standard := normalize_work_type(raw))
    }


def _work_type_states(programme: ProgrammeFacts) -> dict[str, WorkMarkState]:
    return {
        standard: state
        for raw, state in programme.work_type_states.items()
        if (standard := normalize_work_type(raw))
    }


def _entity_work_types(entity: dict[str, Any]) -> set[str]:
    values = entity.get("workInfo") or []
    return {
        standard
        for item in values
        if isinstance(item, dict)
        and (standard := normalize_work_type(str(item.get("workType") or "")))
    }


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    for pattern in (r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", r"(\d{4})(\d{2})(\d{2})"):
        match = re.search(pattern, text)
        if match:
            try:
                return date(*(int(part) for part in match.groups()))
            except ValueError:
                return None
    return None


def _names_match(signature: str, people: set[str]) -> bool:
    name = _compact(signature)
    return bool(name) and any(set(name) & set(_compact(person)) for person in people if person)


def _signature_role(value: str) -> str:
    compact = _compact(value)
    if "被交底" in compact:
        return "被交底人"
    if "交底" in compact:
        return "交底人"
    return compact


def _emergency_role(value: str) -> str:
    compact = _compact(value)
    for role in ("安全员", "组长", "成员"):
        if role in compact:
            return role
    return compact


def _has_month_day(value: str) -> bool:
    if _parse_date(value) is not None:
        return True
    for match in re.finditer(r"(?<!\d)(\d{1,2})\D+(\d{1,2})(?!\d)", value or ""):
        month, day = (int(part) for part in match.groups())
        try:
            date(2000, month, day)
            return True
        except ValueError:
            continue
    return False


def _company_core(value: str) -> str:
    compact = _compact(value)
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "公司", "公章"):
        compact = compact.replace(suffix, "")
    return compact


def _company_names_match(first: str, second: str) -> bool:
    left, right = _company_core(first), _company_core(second)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 4 and (left in right or right in left)


def _visual_for(
    visual: VisualReviewFacts,
    document_type: DocumentType,
    role: str,
    name: str = "",
    used: set[int] | None = None,
) -> SignatureVisualFact | None:
    excluded = used or set()
    candidates = [
        item
        for item in visual.signatures
        if item.document_id == document_type.value and id(item) not in excluded
    ]
    expected_role = _compact(role)

    def role_rank(item: SignatureVisualFact) -> int:
        actual_role = _compact(item.role)
        if actual_role == expected_role:
            return 0
        if _signature_role(actual_role) == _signature_role(expected_role):
            return 1
        if expected_role in actual_role or actual_role in expected_role:
            return 2
        return 3

    candidates = [item for item in candidates if role_rank(item) < 3]
    if not candidates:
        return None
    if name:
        exact_name = [item for item in candidates if _compact(item.name) == _compact(name)]
        if exact_name:
            candidates = exact_name
        else:
            matching_name = [item for item in candidates if _names_match(item.name, {name})]
            if matching_name:
                candidates = matching_name
    return min(candidates, key=role_rank)


class RuleEngine:
    def __init__(self, reference: RuleReferenceData) -> None:
        self.reference = reference

    def evaluate(
        self,
        *,
        entity: dict[str, Any],
        documents: list[DocumentSource],
        programme: ProgrammeFacts | None,
        tech: TechDisclosureFacts | None,
        hot_work: HotWorkFacts | None,
        typos: TypoReviewFacts | None = None,
        visual: VisualReviewFacts | None = None,
        initiation_time: datetime | None = None,
    ) -> ReviewReport:
        visual = visual or VisualReviewFacts()
        typos = typos or TypoReviewFacts()
        raw_results: list[RuleResult] = []
        raw_results.extend(self._programme(entity, documents, programme, typos, visual))
        raw_results.extend(self._tech(programme, tech, typos, visual, initiation_time))
        raw_results.extend(self._hot(programme, hot_work, typos, visual, initiation_time))

        results: list[RuleResult] = []
        for rule in RULES:
            matches = [result for result in raw_results if result.rule_id == rule.rule_id]
            if len(matches) == 1:
                results.append(matches[0])
            elif not matches:
                results.append(_unverifiable(rule.rule_id, "规则未产生审核结果"))
            else:
                failures = [item for item in matches if item.status == RuleStatus.FAIL]
                results.append(
                    failures[0]
                    if failures
                    else _unverifiable(rule.rule_id, f"规则产生{len(matches)}个重复结果")
                )

        reject_failures = [
            r for r in results if r.severity == RuleSeverity.REJECT and r.status == RuleStatus.FAIL
        ]
        reject_unknown = [
            r
            for r in results
            if r.severity == RuleSeverity.REJECT and r.status == RuleStatus.UNVERIFIABLE
        ]
        if reject_failures:
            decision = ReviewDecision.REJECTED
        elif reject_unknown:
            decision = ReviewDecision.MANUAL_REVIEW
        else:
            decision = ReviewDecision.PASSED
        suggestions = [
            r
            for r in results
            if r.severity == RuleSeverity.SUGGESTION and r.status == RuleStatus.FAIL
        ]
        summary = (
            f"共检查{len(results)}项，否决不通过{len(reject_failures)}项，"
            f"需人工复核{len(reject_unknown)}项，建议整改{len(suggestions)}项"
        )
        return ReviewReport(
            review_id=uuid.uuid4().hex,
            decision=decision,
            summary=summary,
            total_files=len(documents),
            reject_count=len(reject_failures),
            suggestion_count=len(suggestions),
            unverifiable_count=len([r for r in results if r.status == RuleStatus.UNVERIFIABLE]),
            results=results,
        )

    def _programme(
        self,
        entity: dict[str, Any],
        documents: list[DocumentSource],
        facts: ProgrammeFacts | None,
        typos: TypoReviewFacts,
        visual: VisualReviewFacts,
    ) -> list[RuleResult]:
        if facts is None:
            return [
                _unverifiable(rule.rule_id, "施工方案未解析")
                for rule in RULES
                if rule.document_type == DocumentType.CONSTRUCTION_PROGRAMME
            ]
        out: list[RuleResult] = []
        typo_items = typos.by_document.get(DocumentType.CONSTRUCTION_PROGRAMME.value)
        if typo_items is None:
            out.append(_unverifiable("P0-TYPO", "未执行错别字检查"))
        else:
            out.append(
                _result(
                    "P0-TYPO",
                    RuleStatus.FAIL if typo_items else RuleStatus.PASS,
                    issue="；".join(f"{x.original}->{x.correction}" for x in typo_items),
                )
            )

        by_title = {section.standard_title: section for section in facts.sections}
        missing: list[str] = []
        empty: list[str] = []
        missing_sub: list[str] = []
        renamed: list[str] = []
        structural_unknown: list[str] = []
        rename_unknown: list[str] = []
        for heading, subheadings in PROGRAMME_HEADINGS.items():
            section = by_title.get(heading)
            if section is None:
                missing.append(heading)
                continue
            if section.content_present is False:
                empty.append(heading)
            elif section.content_present is None:
                structural_unknown.append(f"{heading}内容是否存在无法判断")
            for subheading in subheadings:
                if not any(
                    _contains_alias(observed, (subheading,)) for observed in section.subheadings
                ):
                    missing_sub.append(f"{heading}/{subheading}")
            if section.observed_title and _compact(section.observed_title) != _compact(heading):
                renamed_item = f"{section.observed_title}->{heading}"
                if section.content_equivalent is True:
                    renamed.append(renamed_item)
                elif section.content_equivalent is False:
                    empty.append(f"{renamed_item}内容不符合规范章节")
                else:
                    structural_unknown.append(f"{renamed_item}内容等价性无法判断")
                    rename_unknown.append(renamed_item)
        structural = missing + empty + missing_sub
        if structural:
            out.append(
                _result(
                    "P0-C1",
                    RuleStatus.FAIL,
                    issue="；".join(structural),
                    suggestion="补齐标题及对应内容",
                )
            )
        elif structural_unknown:
            out.append(_unverifiable("P0-C1", "；".join(structural_unknown)))
        else:
            out.append(_result("P0-C1", RuleStatus.PASS))
        if renamed:
            out.append(
                _result(
                    "P0-C2",
                    RuleStatus.FAIL,
                    issue="；".join(renamed),
                    suggestion="将标题改回规范名称",
                )
            )
        elif rename_unknown:
            out.append(_unverifiable("P0-C2", "标题内容等价性无法判断:" + ",".join(rename_unknown)))
        else:
            out.append(_result("P0-C2", RuleStatus.PASS))

        for rule_id in ("P0-H1", "P0-H2", "P0-H3", "P0-H4", "P0-H5"):
            if rule_id == "P0-H5" and facts.specific_chemicals_registered is False:
                out.append(_result(rule_id, RuleStatus.NOT_APPLICABLE, actual="未登记具体化学品"))
                continue
            if rule_id == "P0-H5" and facts.specific_chemicals_registered is None:
                out.append(_unverifiable(rule_id, "无法判断是否登记了具体化学品"))
                continue
            aliases, groups = TABLE_RULES[rule_id]
            table = _find_table(facts.tables, aliases)
            if table is None:
                status = (
                    RuleStatus.UNVERIFIABLE
                    if rule_id == "P0-H5" and facts.specific_chemicals_registered is None
                    else RuleStatus.FAIL
                )
                out.append(
                    _result(
                        rule_id,
                        status,
                        issue="未找到对应表格",
                        confidence=0.0 if status == RuleStatus.UNVERIFIABLE else 1.0,
                    )
                )
                continue
            missing_headers = _missing_headers(table, groups)
            issues = [f"缺少表头:{name}" for name in missing_headers]
            if rule_id == "P0-H1":
                if not table.rows:
                    issues.append("未登记施工工具")
                for row in table.rows:
                    quantity = next((v for k, v in row.items() if "数量" in k), "")
                    tool_name = next((v for k, v in row.items() if "工具" in k), "未命名工具")
                    number = re.search(r"-?\d+(?:\.\d+)?", quantity)
                    if not tool_name.strip():
                        issues.append("存在未填写名称的施工工具")
                        tool_name = "未命名工具"
                    if not quantity.strip() or number is None:
                        issues.append(f"{tool_name}数量为空或无法识别")
                    elif float(number.group()) <= 0:
                        issues.append(f"{tool_name}数量不大于0")
            out.append(
                _result(
                    rule_id,
                    RuleStatus.FAIL if issues else RuleStatus.PASS,
                    issue="；".join(issues),
                    suggestion="补齐表头或有效数量" if issues else "",
                )
            )

        proofreader_visual = _visual_for(visual, DocumentType.CONSTRUCTION_PROGRAMME, "校对人")
        approver_visual = _visual_for(visual, DocumentType.CONSTRUCTION_PROGRAMME, "批复人")
        n1 = _compact(
            facts.proofreader_name or (proofreader_visual.name if proofreader_visual else "")
        )
        n2 = _compact(facts.approver_name or (approver_visual.name if approver_visual else ""))
        if not n1 or not n2:
            out.append(_unverifiable("P0-F1", "校对人或批复人无法识别"))
        else:
            out.append(
                _result(
                    "P0-F1",
                    RuleStatus.FAIL if n1 == n2 else RuleStatus.PASS,
                    issue="校对人与批复人为同一人" if n1 == n2 else "",
                )
            )

        seal = visual.seal
        supplier = str(entity.get("vendorName") or facts.supplier_name)
        seal_text = seal.seal_text or facts.seal_text
        if not seal.available or seal.confidence < MIN_VISUAL_CONFIDENCE:
            out.append(_unverifiable("P0-F2", "缺少可用的公章视觉证据"))
        else:
            seal_ok = _company_names_match(supplier, seal_text)
            known_issues: list[str] = []
            if seal.has_red_seal is False:
                known_issues.append("未检测到红章")
            if seal.in_bottom is False:
                known_issues.append("公章不在底部")
            if supplier and seal_text and not seal_ok:
                known_issues.append("公章文字与供应商不一致")
            if known_issues:
                out.append(
                    _result(
                        "P0-F2",
                        RuleStatus.FAIL,
                        issue="；".join(known_issues),
                        confidence=seal.confidence,
                    )
                )
            elif (
                seal.has_red_seal is None or seal.in_bottom is None or not supplier or not seal_text
            ):
                out.append(_unverifiable("P0-F2", "公章颜色、位置或文字证据不完整"))
            else:
                out.append(_result("P0-F2", RuleStatus.PASS, confidence=seal.confidence))

        sign_issues: list[str] = []
        sign_unknown = False
        for role in ("工程师", "经理"):
            item = _visual_for(visual, DocumentType.CONSTRUCTION_PROGRAMME, role)
            if item is None or item.confidence < MIN_VISUAL_CONFIDENCE:
                sign_unknown = True
                continue
            if item.handwritten is False:
                sign_issues.append(f"{role}不是手写签名")
            elif item.handwritten is None:
                sign_unknown = True
            if item.date_handwritten is False:
                sign_issues.append(f"{role}日期不是手写")
            elif item.date_handwritten is None:
                sign_unknown = True
            if not _has_month_day(item.date):
                sign_issues.append(f"{role}日期缺少有效月日")
        if sign_issues:
            out.append(
                _result(
                    "P0-F3",
                    RuleStatus.FAIL if sign_issues else RuleStatus.PASS,
                    issue="；".join(sign_issues),
                )
            )
        elif sign_unknown:
            out.append(_unverifiable("P0-F3", "工程师或经理签字视觉证据不足"))
        else:
            out.append(_result("P0-F3", RuleStatus.PASS))

        states = _work_type_states(facts)
        blank_states = [name for name in WORK_TYPES if states.get(name) == WorkMarkState.BLANK]
        unknown_states = [
            name
            for name in WORK_TYPES
            if states.get(name) is None or states.get(name) == WorkMarkState.UNKNOWN
        ]
        checked = _checked_types(facts)
        entity_types = _entity_work_types(entity)
        mismatch = sorted(checked ^ entity_types) if entity_types else []
        mark_issues = [f"选项为空:{x}" for x in blank_states]
        if mismatch:
            mark_issues.append(f"与业务作业类型不一致:{','.join(mismatch)}")
        if mark_issues:
            out.append(_result("P0-F4", RuleStatus.FAIL, issue="；".join(mark_issues)))
        elif unknown_states:
            out.append(_unverifiable("P0-F4", "无法识别勾选状态:" + ",".join(unknown_states)))
        else:
            out.append(_result("P0-F4", RuleStatus.PASS))
        hot_state = states.get("动火作业")
        hot_present = any(d.document_type == DocumentType.HOT_WORK_DISCLOSE for d in documents)
        if hot_state == WorkMarkState.UNCHECKED:
            out.append(_result("P0-F5", RuleStatus.NOT_APPLICABLE, actual="施工方案未勾选动火作业"))
        elif hot_state == WorkMarkState.CHECKED:
            out.append(
                _result(
                    "P0-F5",
                    RuleStatus.PASS if hot_present else RuleStatus.FAIL,
                    issue="勾选动火作业但未上传动火交底" if not hot_present else "",
                )
            )
        else:
            out.append(_unverifiable("P0-F5", "施工方案动火作业勾选状态无法确定"))

        if not facts.personnel:
            out.append(_unverifiable("P0-P1", "人员表未提取到人员"))
            out.append(_unverifiable("P0-P2", "人员表未提取到人员"))
        else:
            age_issues: list[str] = []
            cert_issues: list[str] = []
            age_unknown = False
            cert_unknown = False
            for person in facts.personnel:
                if person.age is None or person.has_certificate is None:
                    age_unknown = True
                elif person.age > (55 if person.has_certificate else 60):
                    age_issues.append(f"{person.name}:{person.age}岁")
                status = _compact(person.certificate_status)
                if person.has_certificate is None:
                    cert_unknown = True
                if person.has_certificate is False and status != _compact("无需持证"):
                    cert_issues.append(f"{person.name}:无证但未填写无需持证")
                if person.has_certificate is True and not any(
                    _compact(cert) in status for cert in ALLOWED_CERTIFICATES
                ):
                    cert_issues.append(f"{person.name}:证书类型不在允许清单")
            if age_issues:
                out.append(
                    _result(
                        "P0-P1",
                        RuleStatus.FAIL if age_issues else RuleStatus.PASS,
                        issue="；".join(age_issues),
                    )
                )
            elif age_unknown:
                out.append(_unverifiable("P0-P1", "部分人员年龄或持证状态无法识别"))
            else:
                out.append(_result("P0-P1", RuleStatus.PASS))
            if cert_issues:
                out.append(_result("P0-P2", RuleStatus.FAIL, issue="；".join(cert_issues)))
            elif cert_unknown:
                out.append(_unverifiable("P0-P2", "部分人员持证状态无法识别"))
            else:
                out.append(_result("P0-P2", RuleStatus.PASS))

        start, end = _parse_date(facts.construction_start), _parse_date(facts.construction_end)
        expected_days = entity.get("workDay")
        if not start or not end or not isinstance(expected_days, int):
            out.append(_unverifiable("P0-S1", "施工周期起止或workDay无法确定"))
        else:
            dates = [_parse_date(record.date) for record in facts.daily_records]
            issues: list[str] = []
            if len(facts.daily_records) != expected_days:
                issues.append(f"记录{len(facts.daily_records)}条，应为{expected_days}条")
            if any(item is None for item in dates):
                issues.append("存在无法识别的日期")
            valid_dates = [item for item in dates if item]
            if any(item < start or item > end for item in valid_dates):
                issues.append("存在施工周期外日期")
            if len(set(valid_dates)) != len(valid_dates):
                issues.append("存在重复日期")
            for record in facts.daily_records:
                if not all(
                    (
                        record.activity.strip(),
                        record.participants.strip(),
                        record.precautions.strip(),
                    )
                ):
                    issues.append(f"{record.date}工作内容不完整")
            out.append(
                _result(
                    "P0-S1", RuleStatus.FAIL if issues else RuleStatus.PASS, issue="；".join(issues)
                )
            )

        emergency_section = by_title.get("应急救援预案")
        if emergency_section is None:
            out.append(_result("P0-E1", RuleStatus.FAIL, issue="缺少应急救援预案章节"))
        else:
            required = PROGRAMME_HEADINGS["应急救援预案"]
            missing_emergency = [
                x
                for x in required
                if not any(_contains_alias(y, (x,)) for y in emergency_section.subheadings)
            ]
            out.append(
                _result(
                    "P0-E1",
                    RuleStatus.FAIL if missing_emergency else RuleStatus.PASS,
                    issue="缺少:" + ",".join(missing_emergency) if missing_emergency else "",
                )
            )
        roles = {_emergency_role(item.role) for item in facts.emergency_roles}
        role_missing = [role for role in ("组长", "安全员", "成员") if role not in roles]
        format_issues = role_missing + (
            ["组织架构使用表格，要求按行登记"] if facts.emergency_format.upper() == "TABLE" else []
        )
        if format_issues:
            out.append(
                _result(
                    "P0-E2",
                    RuleStatus.FAIL if format_issues else RuleStatus.PASS,
                    issue="；".join(format_issues),
                )
            )
        elif facts.emergency_format.upper() not in {"LINES", "LINE"}:
            out.append(_unverifiable("P0-E2", "无法判断应急组织是否按行登记"))
        else:
            out.append(_result("P0-E2", RuleStatus.PASS))
        people = {_compact(item.name) for item in facts.personnel if item.name}
        missing_emergency_names = [
            item.role or "未知角色" for item in facts.emergency_roles if not item.name
        ]
        emergency_names = [_compact(item.name) for item in facts.emergency_roles if item.name]
        if missing_emergency_names:
            out.append(
                _result(
                    "P0-E3",
                    RuleStatus.FAIL,
                    issue="缺少姓名:" + ",".join(missing_emergency_names),
                )
            )
        elif not emergency_names or not people:
            out.append(_unverifiable("P0-E3", "应急姓名或人员表姓名无法识别"))
        else:
            outside = [name for name in emergency_names if name not in people]
            out.append(
                _result(
                    "P0-E3",
                    RuleStatus.FAIL if outside else RuleStatus.PASS,
                    issue="不在人员表:" + ",".join(outside) if outside else "",
                )
            )
        if not facts.emergency_roles:
            out.append(_unverifiable("P0-E4", "未提取到应急组织人员"))
        else:
            bad_phones = [
                item.name or item.role
                for item in facts.emergency_roles
                if not re.fullmatch(r"1[3-9]\d{9}", _compact(item.phone))
            ]
            out.append(
                _result(
                    "P0-E4",
                    RuleStatus.FAIL if bad_phones else RuleStatus.PASS,
                    issue="电话无效:" + ",".join(bad_phones) if bad_phones else "",
                )
            )
        return out

    def _tech(
        self,
        programme: ProgrammeFacts | None,
        facts: TechDisclosureFacts | None,
        typos: TypoReviewFacts,
        visual: VisualReviewFacts,
        initiation_time: datetime | None,
    ) -> list[RuleResult]:
        rule_ids = [
            rule.rule_id
            for rule in RULES
            if rule.document_type == DocumentType.CONSTRUCTION_TECH_DISCLOSE
        ]
        if programme is None or facts is None:
            return [_unverifiable(rule_id, "施工方案或安全交底未解析") for rule_id in rule_ids]
        out: list[RuleResult] = []
        typo_items = typos.by_document.get(DocumentType.CONSTRUCTION_TECH_DISCLOSE.value)
        out.append(
            _unverifiable("P1-TYPO", "未执行错别字检查")
            if typo_items is None
            else _result(
                "P1-TYPO",
                RuleStatus.FAIL if typo_items else RuleStatus.PASS,
                issue="；".join(f"{x.original}->{x.correction}" for x in typo_items),
            )
        )
        programme_states = _work_type_states(programme)
        baseline = _checked_types(programme)
        unknown_programme_types = {
            work_type
            for work_type in WORK_TYPES
            if programme_states.get(work_type) in {None, WorkMarkState.UNKNOWN}
        }
        state_evidence_unknown = bool(unknown_programme_types)
        risk = {
            normalize_work_type(k): v
            for k, v in facts.risk_analysis.items()
            if normalize_work_type(k)
        }
        missing_risk = sorted(item for item in baseline if not risk.get(item, "").strip())
        if missing_risk:
            out.append(
                _result("P1-R1", RuleStatus.FAIL, issue="缺少或内容为空:" + ",".join(missing_risk))
            )
        elif state_evidence_unknown:
            out.append(_unverifiable("P1-R1", "施工方案作业类型状态无法完整识别"))
        else:
            out.append(_result("P1-R1", RuleStatus.PASS))
        risk_types = set(risk)
        missing_risk_types = baseline - risk_types
        known_extra_risk = risk_types - baseline - unknown_programme_types
        uncertain_risk = risk_types & unknown_programme_types
        risk_diff = sorted(missing_risk_types | known_extra_risk)
        if risk_diff:
            out.append(
                _result("P1-R2", RuleStatus.FAIL, issue="作业类型不一致:" + ",".join(risk_diff))
            )
        elif state_evidence_unknown or uncertain_risk:
            out.append(_unverifiable("P1-R2", "施工方案作业类型状态无法完整识别"))
        else:
            out.append(_result("P1-R2", RuleStatus.PASS))
        ppe_result = self._ppe("P1-PP", baseline, facts.ppe)
        out.append(
            _unverifiable("P1-PP", "施工方案作业类型状态无法完整识别")
            if state_evidence_unknown and ppe_result.status == RuleStatus.PASS
            else ppe_result
        )
        emergency = {
            normalize_work_type(k): v
            for k, v in facts.emergency_measures.items()
            if normalize_work_type(k)
        }
        missing_em = sorted(item for item in baseline if not emergency.get(item, "").strip())
        extra_em = sorted(set(emergency) - baseline - unknown_programme_types)
        uncertain_em = set(emergency) & unknown_programme_types
        em_issues = (["缺少:" + ",".join(missing_em)] if missing_em else []) + (
            ["多余:" + ",".join(extra_em)] if extra_em else []
        )
        if em_issues:
            out.append(_result("P1-EM", RuleStatus.FAIL, issue="；".join(em_issues)))
        elif state_evidence_unknown or uncertain_em:
            out.append(_unverifiable("P1-EM", "施工方案作业类型状态无法完整识别"))
        else:
            out.append(_result("P1-EM", RuleStatus.PASS))
        states = _work_type_states(facts)
        ck_issues: list[str] = []
        ck_unknown: list[str] = []
        for work_type in WORK_TYPES:
            state = states.get(work_type)
            expected_checked = work_type in baseline
            if state is None or state == WorkMarkState.UNKNOWN:
                ck_unknown.append(work_type)
            elif state == WorkMarkState.BLANK:
                ck_issues.append(f"{work_type}选项为空")
            elif expected_checked and state != WorkMarkState.CHECKED:
                ck_issues.append(f"{work_type}应勾选")
            elif not expected_checked and state != WorkMarkState.UNCHECKED:
                ck_issues.append(f"{work_type}应打叉")
        if ck_issues:
            out.append(_result("P1-CK", RuleStatus.FAIL, issue="；".join(ck_issues)))
        elif ck_unknown or state_evidence_unknown:
            unknown_checks = sorted(set(ck_unknown) | unknown_programme_types)
            out.append(_unverifiable("P1-CK", "无法识别勾选状态:" + ",".join(unknown_checks)))
        else:
            out.append(_result("P1-CK", RuleStatus.PASS))
        out.extend(
            self._signature_rules(
                "P1",
                DocumentType.CONSTRUCTION_TECH_DISCLOSE,
                facts.signatures,
                programme,
                visual,
                initiation_time,
                facts.signature_section_present,
                facts.signatures_complete,
            )
        )
        return out

    def _hot(
        self,
        programme: ProgrammeFacts | None,
        facts: HotWorkFacts | None,
        typos: TypoReviewFacts,
        visual: VisualReviewFacts,
        initiation_time: datetime | None,
    ) -> list[RuleResult]:
        if programme is None:
            return [
                _unverifiable(rule.rule_id, "施工方案未解析，无法判断是否涉及动火")
                for rule in RULES
                if rule.document_type == DocumentType.HOT_WORK_DISCLOSE
            ]
        programme_states = _work_type_states(programme)
        hot_state = programme_states.get("动火作业")
        if hot_state == WorkMarkState.UNCHECKED:
            return [
                _result(rule.rule_id, RuleStatus.NOT_APPLICABLE, actual="未勾选动火作业")
                for rule in RULES
                if rule.document_type == DocumentType.HOT_WORK_DISCLOSE
            ]
        if hot_state != WorkMarkState.CHECKED:
            return [
                _unverifiable(rule.rule_id, "施工方案动火作业勾选状态无法确定")
                for rule in RULES
                if rule.document_type == DocumentType.HOT_WORK_DISCLOSE
            ]
        if facts is None:
            return [
                _unverifiable(rule.rule_id, "动火交底未解析")
                for rule in RULES
                if rule.document_type == DocumentType.HOT_WORK_DISCLOSE
            ]
        out: list[RuleResult] = []
        typo_items = typos.by_document.get(DocumentType.HOT_WORK_DISCLOSE.value)
        out.append(
            _unverifiable("P2-TYPO", "未执行错别字检查")
            if typo_items is None
            else _result(
                "P2-TYPO",
                RuleStatus.FAIL if typo_items else RuleStatus.PASS,
                issue="；".join(f"{x.original}->{x.correction}" for x in typo_items),
            )
        )
        template = self.reference.hot_work_risk_template.strip()
        if not template:
            out.append(_unverifiable("P2-R1", "未配置动火风险标准模板"))
        elif not facts.risk_analysis.strip():
            out.append(_result("P2-R1", RuleStatus.FAIL, issue="风险分析为空"))
        else:
            score = SequenceMatcher(None, _compact(template), _compact(facts.risk_analysis)).ratio()
            out.append(
                _result(
                    "P2-R1",
                    RuleStatus.PASS if score >= 0.8 else RuleStatus.FAIL,
                    issue=f"模板相似度{score:.1%}" if score < 0.8 else "",
                    actual=f"{score:.4f}",
                )
            )
        if not self.reference.hot_work_ppe:
            out.append(_unverifiable("P2-PP", "未配置动火必备防护用品清单"))
        else:
            present = {_compact(item) for item in facts.ppe}
            missing = [
                item
                for item in self.reference.hot_work_ppe
                if not any(_compact(item) in value or value in _compact(item) for value in present)
            ]
            out.append(
                _result(
                    "P2-PP",
                    RuleStatus.FAIL if missing else RuleStatus.PASS,
                    issue="缺少:" + ",".join(missing) if missing else "",
                )
            )
        out.extend(
            self._signature_rules(
                "P2",
                DocumentType.HOT_WORK_DISCLOSE,
                facts.signatures,
                programme,
                visual,
                initiation_time,
                facts.signature_section_present,
                facts.signatures_complete,
            )
        )
        return out

    def _ppe(
        self,
        rule_id: str,
        baseline: set[str],
        actual: dict[str, list[str]],
    ) -> RuleResult:
        if any(not self.reference.ppe_requirements.get(item) for item in baseline):
            missing_config = sorted(
                item for item in baseline if not self.reference.ppe_requirements.get(item)
            )
            return _unverifiable(rule_id, "未配置PPE基准:" + ",".join(missing_config))
        normalized = {
            normalize_work_type(k): {_compact(x) for x in values}
            for k, values in actual.items()
            if normalize_work_type(k)
        }
        missing: list[str] = []
        for work_type in baseline:
            present = normalized.get(work_type, set())
            for required in self.reference.ppe_requirements[work_type]:
                if not any(
                    _compact(required) in item or item in _compact(required) for item in present
                ):
                    missing.append(f"{work_type}/{required}")
        return _result(
            rule_id,
            RuleStatus.FAIL if missing else RuleStatus.PASS,
            issue="缺少:" + ",".join(missing) if missing else "",
        )

    def _signature_rules(
        self,
        prefix: str,
        document_type: DocumentType,
        signatures: list[SignatureFact],
        programme: ProgrammeFacts,
        visual: VisualReviewFacts,
        initiation_time: datetime | None,
        signature_section_present: bool | None,
        signatures_complete: bool | None,
    ) -> list[RuleResult]:
        name_rule, date_rule, range_rule = (
            f"{prefix}-S1",
            f"{prefix}-S2",
            f"{prefix}-S3",
        )
        if not signatures:
            if signature_section_present is False or signatures_complete is True:
                return [
                    _result(name_rule, RuleStatus.FAIL, issue="缺少交底人和被交底人签名"),
                    _result(date_rule, RuleStatus.FAIL, issue="缺少交底人和被交底人签字日期"),
                    _unverifiable(range_rule, "缺少签字日期，无法判断时间范围"),
                ]
            return [
                _unverifiable(name_rule, "未提取到签名"),
                _unverifiable(date_rule, "未提取到签字日期"),
                _unverifiable(range_rule, "未提取到签字日期"),
            ]
        people = {item.name for item in programme.personnel if item.name}
        name_issues: list[str] = []
        name_unknown = not people or signatures_complete is not True
        date_issues: list[str] = []
        date_unknown = signatures_complete is not True
        range_issues: list[str] = []
        range_unknown = initiation_time is None or signatures_complete is not True
        earliest = initiation_time.date() - timedelta(days=7) if initiation_time else None
        latest = initiation_time.date() if initiation_time else None
        observed_roles = {_signature_role(signature.role) for signature in signatures}
        missing_roles = [role for role in ("交底人", "被交底人") if role not in observed_roles]
        if missing_roles:
            message = "缺少:" + ",".join(missing_roles)
            if signatures_complete is True:
                name_issues.append(message + "签名")
                date_issues.append(message + "日期")
                range_unknown = True
            else:
                name_unknown = True
                date_unknown = True
                range_unknown = True

        used_visual: set[int] = set()
        for signature in signatures:
            visual_item = _visual_for(
                visual,
                document_type,
                signature.role,
                signature.name,
                used_visual,
            )
            if visual_item is not None:
                used_visual.add(id(visual_item))
            if (
                visual_item is None
                or visual_item.confidence < MIN_VISUAL_CONFIDENCE
                or visual_item.handwritten is None
            ):
                name_unknown = True
            elif visual_item.handwritten is not True:
                name_issues.append(f"{signature.role}不是手写签名")
            if visual_item is None or visual_item.date_handwritten is None:
                name_unknown = True
            elif visual_item.date_handwritten is not True:
                name_issues.append(f"{signature.role}日期不是手写")
            name = signature.name or (visual_item.name if visual_item else "")
            if not name or not people:
                name_unknown = True
            elif not _names_match(name, people):
                name_issues.append(f"{signature.role}姓名不在人员表")
            parsed = _parse_date(signature.date or (visual_item.date if visual_item else ""))
            if parsed is None:
                date_issues.append(f"{signature.role}日期缺失或不含年月日")
                range_unknown = True
            elif earliest and latest and not (earliest <= parsed <= latest):
                range_issues.append(f"{signature.role}日期不在发起日前7天范围")
        if name_issues:
            name_result = _result(name_rule, RuleStatus.FAIL, issue="；".join(name_issues))
        elif name_unknown:
            name_result = _unverifiable(name_rule, "部分签名缺少视觉证据")
        else:
            name_result = _result(name_rule, RuleStatus.PASS)
        if date_issues:
            date_result = _result(date_rule, RuleStatus.FAIL, issue="；".join(date_issues))
        elif date_unknown:
            date_result = _unverifiable(date_rule, "缺少流程发起时间或日期手写视觉证据")
        else:
            date_result = _result(date_rule, RuleStatus.PASS)
        if range_issues:
            range_result = _result(range_rule, RuleStatus.FAIL, issue="；".join(range_issues))
        elif range_unknown:
            range_result = _unverifiable(range_rule, "缺少流程发起时间或完整签字日期")
        else:
            range_result = _result(range_rule, RuleStatus.PASS)
        return [name_result, date_result, range_result]


__all__ = [
    "ALLOWED_CERTIFICATES",
    "PROGRAMME_HEADINGS",
    "RuleEngine",
    "WORK_TYPES",
    "WORK_TYPE_ALIASES",
    "normalize_work_type",
]
