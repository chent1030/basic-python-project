"""Small set of extraction agents for document review.

Agents extract uncertain facts from OCR. They never decide pass/fail; the
deterministic RuleEngine owns every business decision.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app.harness import BaseSingleAgent
from app.projects.doc_review.schemas import (
    HotWorkFacts,
    ProgrammeFacts,
    TechDisclosureFacts,
    TypoReviewFacts,
)

T = TypeVar("T", bound=BaseModel)


def parse_model_output(text: str, model: type[T]) -> T:
    """Parse a model response without accepting partial or invented defaults."""
    stripped = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        return model.model_validate_json(stripped)
    except Exception:
        decoder = json.JSONDecoder()
        for index, char in enumerate(stripped):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                return model.model_validate(value)
            except (json.JSONDecodeError, ValueError):
                continue
        raise ValueError(f"模型输出不是有效的 {model.__name__} JSON") from None


class _FactsAgent(BaseSingleAgent):
    backend = "deepagents"
    middleware = ["tracing"]
    recursion_limit = 30
    temperature = 0.0
    blackboard_key = ""
    schema_model: type[BaseModel]
    domain_instructions = ""
    tools = [
        "fetch_result",
        "extract_markdown_tables",
        "extract_work_checks",
        "extract_kv_lines",
        "normalize_heading",
    ]

    @property
    def system_prompt(self) -> str:
        schema = json.dumps(self.schema_model.model_json_schema(), ensure_ascii=False)
        return (
            "你是工程文档事实提取专家，不负责判断合规与否。\n"
            f"先调用 fetch_result(key={self.blackboard_key}) 取得 OCR Markdown。"
            "可以调用表格、勾选、键值和标题解析工具辅助提取。\n"
            "只根据文档证据填写；无法识别时使用空字符串、空数组、null或UNKNOWN，"
            "不得猜测。表格rows必须转成以表头为键的对象。\n"
            "勾选状态定义：CHECKED仅表示√/✓/✔/☑/☒；UNCHECKED仅表示×/✕/✖；"
            "BLANK仅表示☐/□空框；没有符号或无法确定时必须为UNKNOWN。\n"
            f"{self.domain_instructions}\n"
            f"只输出符合此JSON Schema的对象，不要Markdown代码块：{schema}"
        )


class ProgrammeFactsAgent(_FactsAgent):
    name = "programme_facts"
    blackboard_key = "ocr_programme"
    schema_model = ProgrammeFacts
    domain_instructions = (
        "提取十个规范章节，standard_title使用规范名称，observed_title保留原文；"
        "content_present表示标题下是否有实际内容，content_equivalent表示改名标题内容是否"
        "等同规范章节。提取全部十类作业状态，值只能为CHECKED/UNCHECKED/BLANK/UNKNOWN。"
        "人员、施工周期、每日记录、化学品是否登记、应急组织按行或表格格式均需提取。"
        "emergency_format只能使用LINES、TABLE或UNKNOWN。"
    )


class TechDisclosureFactsAgent(_FactsAgent):
    name = "tech_disclosure_facts"
    blackboard_key = "ocr_tech_disclose"
    schema_model = TechDisclosureFacts
    domain_instructions = (
        "risk_analysis按作业类型映射到该类型的风险内容；ppe按作业类型映射到用品列表；"
        "emergency_measures按作业类型映射到处置内容。提取全部作业类型的四态勾选和"
        "交底人、被交底人签名与完整日期。signature_section_present表示是否确认存在"
        "签字区；signatures_complete只有在签字区完整可读且每条签名均已提取时才为true。"
    )


class HotWorkFactsAgent(_FactsAgent):
    name = "hot_work_facts"
    blackboard_key = "ocr_hotwork"
    schema_model = HotWorkFacts
    domain_instructions = (
        "完整保留风险分析文本，提取全部PPE及交底人、被交底人签名日期。"
        "signature_section_present表示是否确认存在签字区；signatures_complete只有在"
        "签字区完整可读且每条签名均已提取时才为true。"
    )


class TypoFactsAgent(BaseSingleAgent):
    name = "typo_facts"
    backend = "deepagents"
    middleware = ["tracing"]
    recursion_limit = 30
    temperature = 0.0
    tools = ["fetch_result"]

    @property
    def system_prompt(self) -> str:
        schema = json.dumps(TypoReviewFacts.model_json_schema(), ensure_ascii=False)
        return (
            "你是中文工程文档校对专家。分别调用fetch_result取得ocr_programme、"
            "ocr_tech_disclose、ocr_hotwork和biz_data。检查三份实际存在文档的错别字，"
            "明确排除biz_data.person_names中的姓名文本。不存在的文档不要产生键。"
            "by_document键只能使用CONSTRUCTION_PROGRAMME、"
            "CONSTRUCTION_TECH_DISCLOSE、HOT_WORK_DISCLOSE。"
            f"只输出JSON，不要解释。Schema:{schema}"
        )


__all__ = [
    "HotWorkFactsAgent",
    "ProgrammeFactsAgent",
    "TechDisclosureFactsAgent",
    "TypoFactsAgent",
    "parse_model_output",
]
