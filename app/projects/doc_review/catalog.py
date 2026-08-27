"""Executable rule catalog derived from ``deman.md``."""

from __future__ import annotations

from dataclasses import dataclass

from app.projects.doc_review.schemas import DocumentType, RuleSeverity


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    name: str
    severity: RuleSeverity
    document_type: DocumentType
    requirement: str


def _r(
    rule_id: str,
    name: str,
    severity: RuleSeverity,
    document_type: DocumentType,
    requirement: str,
) -> RuleSpec:
    return RuleSpec(rule_id, name, severity, document_type, requirement)


P0 = DocumentType.CONSTRUCTION_PROGRAMME
P1 = DocumentType.CONSTRUCTION_TECH_DISCLOSE
P2 = DocumentType.HOT_WORK_DISCLOSE
REJECT = RuleSeverity.REJECT
SUGGEST = RuleSeverity.SUGGESTION
REMIND = RuleSeverity.REMINDER

RULES: tuple[RuleSpec, ...] = (
    _r("P0-TYPO", "施工方案错别字", REMIND, P0, "全文无错别字，姓名除外"),
    _r("P0-C1", "十章及二级标题内容完整", REJECT, P0, "十个一级标题、规定二级标题及内容完整"),
    _r("P0-C2", "标题名称规范", SUGGEST, P0, "标题改变但内容基本相同时给出修改建议"),
    _r("P0-H1", "施工工具表", SUGGEST, P0, "含工具和数量，数量为0的工具单独列出"),
    _r("P0-H2", "现场作业人员表头", REJECT, P0, "含人数、姓名、工种、持证情况、年龄"),
    _r("P0-H3", "风险评估表头", REJECT, P0, "风险评估表七类信息完整"),
    _r("P0-H4", "每日工作表头", REJECT, P0, "含日期、活动、参与人员、注意事项"),
    _r("P0-H5", "化学品表头", REJECT, P0, "登记具体化学品时含名称、数量、存储规定、危险"),
    _r("P0-F1", "校对人与批复人不同", REJECT, P0, "两人不得相同"),
    _r("P0-F2", "底部红章与供应商一致", REJECT, P0, "底部红章文字与供应商一致"),
    _r("P0-F3", "工程师经理手写签字日期", REJECT, P0, "工程师和经理均手写签字并至少填写月日"),
    _r("P0-F4", "作业类型勾选完整", REJECT, P0, "所有作业类型明确为勾选或未勾选"),
    _r("P0-F5", "动火交底文件已上传", REJECT, P0, "动火作业勾选时上传动火交底"),
    _r("P0-P1", "人员年龄合规", REJECT, P0, "持证不大于55岁，未持证不大于60岁"),
    _r("P0-P2", "持证情况填写规范", SUGGEST, P0, "无证填无需持证，证书类型在允许清单"),
    _r("P0-S1", "每日记录覆盖施工周期", REJECT, P0, "记录数等于周期天数且日期均在周期内"),
    _r("P0-E1", "应急三个二级标题", REJECT, P0, "组织架构、职责电话、事故处置流程齐全"),
    _r("P0-E2", "应急角色及格式", REJECT, P0, "按行登记组长、安全员、成员，不使用表格"),
    _r("P0-E3", "应急姓名属于作业人员", REJECT, P0, "全部应急成员出现在人员表"),
    _r("P0-E4", "应急人员有效电话", REJECT, P0, "每个人名后有有效11位手机号码"),
    _r("P1-TYPO", "安全交底错别字", REMIND, P1, "全文无错别字，姓名除外"),
    _r("P1-R1", "风险分析覆盖勾选作业", REJECT, P1, "每个施工方案勾选作业都有非空风险分析"),
    _r("P1-R2", "风险作业名称映射正确", REJECT, P1, "名称符合施工方案与交底映射且无多余类型"),
    _r("P1-PP", "防护用品完整", REJECT, P1, "各勾选作业必备防护用品完整"),
    _r("P1-EM", "应急措施完整", REJECT, P1, "覆盖全部勾选作业且无未勾选作业"),
    _r("P1-CK", "作业类型勾选一致", REJECT, P1, "勾选与施工方案一致，其余全部明确打叉"),
    _r(
        "P1-S1",
        "签名日期手写且属于人员表",
        REJECT,
        P1,
        "全部人名和日期均手写，姓名与人员表至少一字匹配",
    ),
    _r("P1-S2", "签字日期含年月日", REJECT, P1, "签字日期必须包含年月日"),
    _r("P1-S3", "签字日期时间范围", SUGGEST, P1, "签字日期在流程发起当日及之前7天内"),
    _r("P2-TYPO", "动火交底错别字", REMIND, P2, "全文无错别字，姓名除外"),
    _r("P2-R1", "动火风险模板完整度", REJECT, P2, "与标准模板相似度不低于80%"),
    _r("P2-PP", "动火防护用品完整", REJECT, P2, "登记全部动火必备用品"),
    _r(
        "P2-S1",
        "动火签名日期手写且属于人员表",
        REJECT,
        P2,
        "全部人名和日期均手写且姓名属于人员表",
    ),
    _r("P2-S2", "动火签字日期含年月日", REJECT, P2, "签字日期必须包含年月日"),
    _r("P2-S3", "动火签字日期时间范围", SUGGEST, P2, "签字日期在流程发起当日及之前7天内"),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}


def rules_for(document_type: DocumentType) -> list[RuleSpec]:
    return [rule for rule in RULES if rule.document_type == document_type]


__all__ = ["RULES", "RULE_BY_ID", "RuleSpec", "rules_for"]
