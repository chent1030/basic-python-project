"""Deterministic construction-programme rule evaluation."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .models import (
    ConstructionFacts,
    FindingSeverity,
    FindingStatus,
    RuleFinding,
)

SECTION_RULES: dict[str, tuple[str, ...]] = {
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

TABLE_RULES: dict[str, tuple[tuple[str, ...], ...]] = {
    "施工工具": (("工具", "施工工具"), ("数量",)),
    "现场作业人员": (("人数", "人员数量"), ("姓名",), ("工种",), ("持证情况", "证书"), ("年龄",)),
    "风险评估": (
        ("活动",),
        ("危险源",),
        ("风险评估",),
        ("风险等级",),
        ("风险处置措施", "处置措施"),
        ("采取措施后的风险评估", "措施后风险评估"),
        ("处置后风险等级", "处置后等级"),
    ),
    "每天工作": (("日期",), ("活动",), ("参与人员",), ("注意事项",)),
    "化学品": (("化学品名称", "名称"), ("数量",), ("存储规定", "储存规定"), ("危险", "危害")),
}

ALLOWED_CERTIFICATES = {"电工证", "焊工证", "焊接证", "登高证", "安全员证"}
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


def _compact(value: str) -> str:
    return re.sub(r"[\s:：、，,；;（）()\-_/&]", "", value or "")


def _contains(value: str, aliases: tuple[str, ...]) -> bool:
    compact = _compact(value)
    return bool(compact) and any(_compact(alias) in compact for alias in aliases)


def _company_core(value: str) -> str:
    result = _compact(value)
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "公司", "公章"):
        result = result.replace(suffix, "")
    return result


def _company_match(first: str, second: str) -> bool:
    left, right = _company_core(first), _company_core(second)
    return bool(left and right) and (left == right or left in right or right in left)


def _date_values(value: str) -> list[date]:
    dates: list[date] = []
    for match in re.finditer(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value or ""):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return dates


def _finding(
    rule_id: str,
    severity: FindingSeverity,
    status: FindingStatus,
    *,
    message: str = "",
    expected: str = "",
    actual: str = "",
    confidence: float = 1.0,
    retryable: bool = False,
) -> RuleFinding:
    return RuleFinding(
        rule_id=rule_id,
        severity=severity,
        status=status,
        message=message,
        expected=expected,
        actual=actual,
        confidence=confidence,
        retryable=retryable,
    )


class ConstructionPlanRuleEngine:
    """Evaluate the construction-plan subset of ``deman.md``."""

    def evaluate(self, *, entity: dict[str, Any], facts: ConstructionFacts) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        findings.extend(self._sections(facts))
        findings.append(self._heading_names(facts))
        findings.extend(self._tables(facts))
        findings.extend(self._cover(entity, facts))
        findings.extend(self._personnel(facts))
        findings.extend(self._schedule(entity, facts))
        findings.extend(self._emergency(facts))
        findings.append(self._typos(facts))
        return findings

    def _sections(self, facts: ConstructionFacts) -> list[RuleFinding]:
        if not facts.sections:
            return [
                _finding(
                    "CP-C1",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="无法识别章节结构",
                    confidence=0.0,
                    retryable=True,
                )
            ]
        by_title = {_compact(item.standard_title): item for item in facts.sections}
        missing: list[str] = []
        incomplete: list[str] = []
        for title, subheadings in SECTION_RULES.items():
            item = by_title.get(_compact(title))
            if item is None:
                missing.append(title)
                continue
            if item.content_present is False:
                incomplete.append(title)
            observed = {_compact(value) for value in item.subheadings}
            incomplete.extend(
                f"{title}/{subheading}"
                for subheading in subheadings
                if not any(_compact(subheading) in value for value in observed)
            )
        if missing or incomplete:
            return [
                _finding(
                    "CP-C1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="施工方案章节或章节内容不完整",
                    expected="十个一级标题及规定二级标题内容完整",
                    actual=f"缺失: {missing}; 不完整: {incomplete}",
                )
            ]
        return [_finding("CP-C1", FindingSeverity.REJECT, FindingStatus.PASS)]

    def _tables(self, facts: ConstructionFacts) -> list[RuleFinding]:
        results: list[RuleFinding] = []
        for index, (name, groups) in enumerate(TABLE_RULES.items(), start=1):
            rule_id = f"CP-H{index}"
            severity = FindingSeverity.SUGGESTION if name == "施工工具" else FindingSeverity.REJECT
            tables = [table for table in facts.tables if _contains(table.name, (name,))]
            if name == "化学品" and facts.specific_chemicals_registered is False:
                results.append(_finding(rule_id, severity, FindingStatus.PASS))
                continue
            if name == "化学品" and not facts.tables:
                results.append(
                    _finding(
                        rule_id,
                        severity,
                        FindingStatus.REVIEW_REQUIRED,
                        message="无法判断化学品登记表",
                        confidence=0.0,
                        retryable=True,
                    )
                )
                continue
            if not tables:
                results.append(
                    _finding(
                        rule_id,
                        severity,
                        FindingStatus.FAIL,
                        message=f"缺少{name}表格",
                    )
                )
                continue
            joined = "|".join(tables[0].headers)
            missing = ["/".join(group) for group in groups if not _contains(joined, group)]
            if missing:
                results.append(
                    _finding(
                        rule_id,
                        FindingSeverity.REJECT,
                        FindingStatus.FAIL,
                        message=f"{name}表头不完整",
                        actual="、".join(missing),
                    )
                )
            else:
                zero_tools = self._zero_quantity_tools(tables[0]) if name == "施工工具" else []
                results.append(
                    _finding(
                        rule_id,
                        severity,
                        FindingStatus.FAIL if zero_tools else FindingStatus.PASS,
                        message="施工工具数量为0" if zero_tools else "",
                        actual="、".join(zero_tools),
                    )
                )
        return results

    @staticmethod
    def _zero_quantity_tools(table: Any) -> list[str]:
        names: list[str] = []
        for row in table.rows:
            quantity = next(
                (value for key, value in row.items() if "数量" in _compact(key)),
                "",
            )
            if str(quantity).strip() not in {"0", "0.0", "0件", "0个", "0台"}:
                continue
            tool = next(
                (value for key, value in row.items() if "工具" in _compact(key)),
                "未命名工具",
            )
            names.append(str(tool))
        return names

    @staticmethod
    def _heading_names(facts: ConstructionFacts) -> RuleFinding:
        changed = [
            item.standard_title
            for item in facts.sections
            if item.content_equivalent is True
            and _compact(item.standard_title) != _compact(item.observed_title)
        ]
        return _finding(
            "CP-C2",
            FindingSeverity.SUGGESTION,
            FindingStatus.FAIL if changed else FindingStatus.PASS,
            message="标题修改但内容未变化" if changed else "",
            actual="、".join(changed),
        )

    def _cover(self, entity: dict[str, Any], facts: ConstructionFacts) -> list[RuleFinding]:
        results: list[RuleFinding] = []
        if (
            facts.proofreader_name
            and facts.approver_name
            and _compact(facts.proofreader_name) == _compact(facts.approver_name)
        ):
            results.append(
                _finding(
                    "CP-F1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="校对人员与批复人员相同",
                )
            )
        elif not facts.proofreader_name or not facts.approver_name:
            results.append(
                _finding(
                    "CP-F1",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="无法完整识别校对人员或批复人员",
                    confidence=0.0,
                    retryable=True,
                )
            )
        else:
            results.append(_finding("CP-F1", FindingSeverity.REJECT, FindingStatus.PASS))

        seal = facts.seal
        if seal.present is False:
            results.append(
                _finding(
                    "CP-F2", FindingSeverity.REJECT, FindingStatus.FAIL, message="未识别到公章"
                )
            )
        elif seal.present is None or seal.is_red is None:
            results.append(
                _finding(
                    "CP-F2",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="公章或红色属性无法确认",
                    confidence=0.0,
                    retryable=True,
                )
            )
        elif not seal.is_red:
            results.append(
                _finding(
                    "CP-F2", FindingSeverity.REJECT, FindingStatus.FAIL, message="公章不是红色"
                )
            )
        elif not seal.seal_text or not _company_match(
            seal.seal_text, str(entity.get("vendorName") or "")
        ):
            results.append(
                _finding(
                    "CP-F2",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="公章内容与供应商名称不一致或无法匹配",
                    actual=seal.seal_text,
                )
            )
        else:
            results.append(_finding("CP-F2", FindingSeverity.REJECT, FindingStatus.PASS))

        required_roles = {"工程师", "经理"}
        signatures = {signature.role: signature for signature in facts.signatures}
        missing = required_roles - set(signatures)
        invalid = [
            role
            for role in required_roles & set(signatures)
            if signatures[role].handwritten is not True
            or not self._has_month_day(signatures[role].date)
        ]
        if missing or invalid:
            results.append(
                _finding(
                    "CP-F3",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="工程师或经理手写签字日期无法确认",
                    actual=f"缺失={sorted(missing)},不合规={sorted(invalid)}",
                    confidence=0.0,
                    retryable=True,
                )
            )
        else:
            results.append(_finding("CP-F3", FindingSeverity.REJECT, FindingStatus.PASS))

        states = facts.work_type_states
        unknown = [
            work_type
            for work_type in WORK_TYPES
            if states.get(work_type) not in {"CHECKED", "UNCHECKED"}
        ]
        results.append(
            _finding(
                "CP-F4",
                FindingSeverity.REJECT,
                FindingStatus.FAIL if unknown else FindingStatus.PASS,
                message="作业类型存在未明确的勾选状态" if unknown else "",
                actual="、".join(unknown),
            )
        )
        return results

    def _personnel(self, facts: ConstructionFacts) -> list[RuleFinding]:
        age_issues: list[str] = []
        cert_issues: list[str] = []
        for person in facts.personnel:
            if person.age is None or person.has_certificate is None:
                age_issues.append(person.name or "未命名人员")
                continue
            limit = 55 if person.has_certificate else 60
            if person.age > limit:
                age_issues.append(f"{person.name}({person.age}岁>{limit}岁)")
            status = person.certificate_status.strip()
            if person.has_certificate and status not in ALLOWED_CERTIFICATES:
                cert_issues.append(f"{person.name}: {status or '缺少证书类型'}")
            if not person.has_certificate and status != "无需持证":
                cert_issues.append(f"{person.name}: 未持证应填写无需持证，当前为{status or '空'}")
        return [
            _finding(
                "CP-P1",
                FindingSeverity.REJECT,
                FindingStatus.REVIEW_REQUIRED
                if age_issues and not any("岁>" in item for item in age_issues)
                else (FindingStatus.FAIL if age_issues else FindingStatus.PASS),
                message="人员年龄不符合要求" if age_issues else "",
                actual="、".join(age_issues),
                retryable=bool(age_issues and not any("岁>" in item for item in age_issues)),
            ),
            _finding(
                "CP-P2",
                FindingSeverity.SUGGESTION,
                FindingStatus.FAIL if cert_issues else FindingStatus.PASS,
                message="持证情况填写不规范" if cert_issues else "",
                actual="、".join(cert_issues),
            ),
        ]

    def _schedule(self, entity: dict[str, Any], facts: ConstructionFacts) -> list[RuleFinding]:
        expected = entity.get("workDay")
        if not isinstance(expected, int) or expected < 0:
            return [
                _finding(
                    "CP-S1",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="施工周期天数缺失或不合法",
                    confidence=0.0,
                )
            ]
        if len(facts.daily_records) != expected:
            return [
                _finding(
                    "CP-S1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="每日工作记录数量与施工周期不一致",
                    expected=str(expected),
                    actual=str(len(facts.daily_records)),
                )
            ]
        work_dates = [
            item
            for work in entity.get("workInfo") or []
            if isinstance(work, dict)
            for item in _date_values(str(work.get("workDate") or ""))
        ]
        record_dates = [
            item for record in facts.daily_records for item in _date_values(record.date)
        ]
        if (
            work_dates
            and record_dates
            and any(item < min(work_dates) or item > max(work_dates) for item in record_dates)
        ):
            return [
                _finding(
                    "CP-S1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="每日工作记录存在施工周期外日期",
                )
            ]
        if not record_dates and expected:
            return [
                _finding(
                    "CP-S1",
                    FindingSeverity.REJECT,
                    FindingStatus.REVIEW_REQUIRED,
                    message="每日工作记录日期无法解析",
                    confidence=0.0,
                    retryable=True,
                )
            ]
        return [_finding("CP-S1", FindingSeverity.REJECT, FindingStatus.PASS)]

    def _emergency(self, facts: ConstructionFacts) -> list[RuleFinding]:
        required = {"组长", "安全员", "成员"}
        roles = {item.role for item in facts.emergency_roles}
        required_subheadings = {
            "应急组织架构",
            "各成员职责和联系电话",
            "各项风险事故的应急处置流程",
        }
        observed_subheadings = {_compact(item) for item in facts.emergency_subheadings}
        missing_subheadings = [
            item
            for item in required_subheadings
            if not any(_compact(item) in value for value in observed_subheadings)
        ]
        if facts.emergency_format != "BY_LINE" or not required <= roles:
            return [
                _finding(
                    "CP-E1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="应急组织架构格式或角色不完整",
                )
            ]
        if missing_subheadings:
            return [
                _finding(
                    "CP-E1",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="应急预案二级标题不完整",
                    actual="、".join(missing_subheadings),
                )
            ]
        personnel_names = {_compact(item.name) for item in facts.personnel if item.name}
        missing_names = [
            item.name or item.role
            for item in facts.emergency_roles
            if not item.name
            or not any(
                _compact(item.name) in person or person in _compact(item.name)
                for person in personnel_names
            )
        ]
        if missing_names:
            return [
                _finding(
                    "CP-E2",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="应急成员姓名未出现在现场作业人员表",
                    actual="、".join(missing_names),
                )
            ]
        invalid = [
            item.name or item.role
            for item in facts.emergency_roles
            if not re.fullmatch(r"1\d{10}", item.phone or "")
        ]
        if invalid:
            return [
                _finding(
                    "CP-E3",
                    FindingSeverity.REJECT,
                    FindingStatus.FAIL,
                    message="应急人员联系电话不是有效11位手机号",
                    actual="、".join(invalid),
                )
            ]
        return [
            _finding("CP-E1", FindingSeverity.REJECT, FindingStatus.PASS),
            _finding("CP-E2", FindingSeverity.REJECT, FindingStatus.PASS),
            _finding("CP-E3", FindingSeverity.REJECT, FindingStatus.PASS),
        ]

    def _typos(self, facts: ConstructionFacts) -> RuleFinding:
        return _finding(
            "CP-TYPO",
            FindingSeverity.REMINDER,
            FindingStatus.FAIL if facts.typos else FindingStatus.PASS,
            message="发现可能的错别字" if facts.typos else "",
            actual=str(facts.typos),
        )

    @staticmethod
    def _has_month_day(value: str) -> bool:
        if _date_values(value):
            return True
        return bool(re.search(r"(?<!\d)\d{1,2}\D+\d{1,2}(?!\d)", value or ""))


__all__ = ["ConstructionPlanRuleEngine", "WORK_TYPES"]
