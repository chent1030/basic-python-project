"""文档审核 agent 定义 —— 继承 harness 基类。

每个 agent 继承拓扑基类(BaseSingleAgent),设类属性声明配置。
后端可切换(backend 类属性):deepagents / agentscope / llm。

业务用法:
    from app.projects.doc_review.agents import DocReviewFlow
    flow = DocReviewFlow()
    result = await flow.run("审核任务")
"""
from __future__ import annotations

from app.harness import (
    BasePipelineAgent,
    BaseSingleAgent,
    PipelineStep,
    tool,
)

# ===========================================================================
# 工具(规则检查,纯代码 @tool)
# ===========================================================================

@tool("check_name_conflict")
async def check_name_conflict(name1: str, name2: str) -> str:
    """检查两个人名是否为同一人(校对人 vs 批复人不得相同)。

    Args:
        name1: 第一个人名(如校对人)。
        name2: 第二个人名(如批复人)。
    """
    n1 = name1.strip()
    n2 = name2.strip()
    if not n1 or not n2:
        return '{"pass": true, "detail": "存在未填写人员,跳过"}'
    if n1 == n2:
        return f'{{"pass": false, "detail": "{name1} 与 {name2} 为同一人"}}'
    return f'{{"pass": true, "detail": "校对人 {name1} 与批复人 {name2} 不为同一人"}}'


@tool("check_seal_supplier")
async def check_seal_supplier(seal_text: str, supplier_name: str) -> str:
    """检查公章文字是否与供应商名称一致。

    Args:
        seal_text: OCR 识别出的公章文字。
        supplier_name: 供应商名称(比对基准)。
    """
    if not seal_text:
        return '{"pass": false, "detail": "未检测到公章文字"}'
    if not supplier_name:
        return '{"pass": true, "detail": "未提供供应商名称,跳过"}'
    if supplier_name in seal_text or seal_text in supplier_name:
        return f'{{"pass": true, "detail": "公章 {seal_text} 与供应商 {supplier_name} 一致"}}'
    return f'{{"pass": false, "detail": "公章 {seal_text} 与供应商 {supplier_name} 不一致"}}'


# ===========================================================================
# OCR 排序 agent
# ===========================================================================

class OcrSorter(BaseSingleAgent):
    """调 mineru_ocr 工具做 OCR + 恢复页面顺序。"""

    name = "ocr_sorter"
    backend = "deepagents"
    system_prompt = (
        "你是文档 OCR 排序专家。"
        "1. 用 mineru_ocr 工具对文件 URL 做 OCR。"
        "2. 检查页面顺序,恢复正确顺序。"
        "3. 输出排序后的完整文档内容。"
    )
    tools = ["mineru_ocr"]
    recursion_limit = 30
    middleware = ["tracing"]


# ===========================================================================
# AI 检查 agent(deepagents 后端,深度推理)
# ===========================================================================

class TypoChecker(BaseSingleAgent):
    """错别字检查。"""

    name = "typo"
    backend = "deepagents"
    system_prompt = (
        "你是文字校对专家。检查文档中的错别字。"
        '只输出 JSON: {"pass": true/false, "issues": ["位置:错误->正确"], '
        '"suggestion": "总结"}'
    )
    middleware = ["tracing"]


class HeadingChecker(BaseSingleAgent):
    """大小标题完整性检查。"""

    name = "heading"
    backend = "deepagents"
    system_prompt = (
        "你是文档结构检查专家。检查大小标题是否完整、层级合理、编号连续。"
        '只输出 JSON: {"pass": true/false, "issues": ["缺失标题"], "suggestion": "..."}'
    )
    middleware = ["tracing"]


class TableChecker(BaseSingleAgent):
    """表格结构检查。"""

    name = "table"
    backend = "deepagents"
    system_prompt = (
        "你是表格检查专家。检查表格是否缺行缺列、关键单元格空缺、格式规范。"
        '只输出 JSON: {"pass": true/false, "issues": [], "suggestion": "..."}'
    )
    middleware = ["tracing"]


class CoverChecker(BaseSingleAgent):
    """封面等级规范检查。"""

    name = "cover"
    backend = "deepagents"
    system_prompt = (
        "你是封面规范检查专家。检查封面等级信息是否完整、格式规范。"
        '只输出 JSON: {"pass": true/false, "issues": [], "suggestion": "..."}'
    )
    middleware = ["tracing"]


# ===========================================================================
# 规则检查 agent(llm 后端,调工具做确定性判断)
# ===========================================================================

class SealChecker(BaseSingleAgent):
    """公章检查(规则类)。"""

    name = "seal"
    backend = "deepagents"
    system_prompt = (
        "你是公章检查 agent。用 check_seal_supplier 工具检查公章文字是否与供应商一致。"
        "从文档内容和业务数据提取公章文字和供应商名,调工具判断。"
        '汇总输出 JSON: {"pass": true/false, "issues": [], "suggestion": "..."}'
    )
    tools = ["check_seal_supplier"]
    middleware = ["tracing"]


class SignerChecker(BaseSingleAgent):
    """校对人/批复人检查(规则类)。"""

    name = "signer"
    backend = "deepagents"
    system_prompt = (
        "你是人员校验 agent。用 check_name_conflict 工具检查校对人与批复人是否同一人。"
        "从文档提取姓名,调工具判断。"
        '输出 JSON: {"pass": true/false, "issues": [], "suggestion": "..."}'
    )
    tools = ["check_name_conflict"]
    middleware = ["tracing"]


# ===========================================================================
# 报告汇总 agent
# ===========================================================================

class ReportWriter(BaseSingleAgent):
    """汇总所有检查结果生成报告。"""

    name = "report_writer"
    backend = "deepagents"
    system_prompt = (
        "你是审核报告生成专家。整合各项检查结果,生成结构化报告。"
        "输出 JSON: "
        '{"overall_pass": true/false, "summary": "共检查N项,X项不通过",'
        ' "checks": [{"name","pass","severity","issues","suggestion"}],'
        ' "overall_suggestion": "..."}'
    )
    middleware = ["tracing"]


# ===========================================================================
# 文档审核流水线(pipeline 拓扑:OCR → 并行检查 → 报告)
# ===========================================================================

class DocReviewFlow(BasePipelineAgent):
    """文档审核流水线。

    流程:OCR+排序 → [错别字/标题/表格/封面/公章/人员 并行检查] → 汇总报告。
    业务调用:flow = DocReviewFlow(); result = await flow.run("文件URL")
    """

    name = "doc_review"
    backend = "deepagents"
    steps = [
        PipelineStep(run=OcrSorter, name="ocr"),
        PipelineStep(
            parallel=[TypoChecker, HeadingChecker, TableChecker, CoverChecker,
                      SealChecker, SignerChecker],
            aggregator="merge",
            name="checks",
        ),
        PipelineStep(run=ReportWriter, name="report"),
    ]
    middleware = ["tracing"]
