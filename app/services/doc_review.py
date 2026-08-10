"""文档智能审核 service —— 编排核心:OCR → 章节提取 → 并行检查 → 汇总报告。

流程:
1. 调 doc_ocr_sorter agent(它内部调 mineru_ocr 工具)拿到 OCR + 排序后的文档内容
2. 提取章节(按标题自动拆 + 封面/末尾固定区域)
3. 并行触发各检查 agent(每项只拿「所需章节 + entity 业务数据」,不全传)
4. 汇总所有检查结果,调 review_reporter 生成结构化报告
5. 若提供 callback_url,把结果 POST 回外部系统

entity(业务数据)是比对基准:供应商名、预期人员、项目信息等。
每个检查在 CHECKS 里声明需要的章节(sections),service 只传对应章节,省 token 且精准。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging_config import get_logger

log = get_logger("app.services.doc_review")

# 检查项定义:check_name -> (agent_name, check_type, task_description, sections)
# check_type: "ai"(LLM 判断) | "rule"(代码判断,agent 调工具)
# task_description: 告诉检查 agent 要检查什么(拼进 prompt)
# sections: 该检查需要的章节名列表。特殊值:
#   "full"  = 全文(不按章节)
#   "cover" = 封面区(第一个标题前的内容)
#   "tail"  = 末尾区(签字盖章,最后一段)
#   其它字符串 = 按标题文本匹配(模糊匹配,如 "项目概况" 匹配 "## 一、项目概况")
CHECKS: dict[str, tuple[str, str, str, list[str]]] = {
    "typo": ("typo_checker", "ai", "检查文档中的错别字", ["full"]),
    "heading": ("heading_checker", "ai", "检查大小标题是否完整、层级是否合理", ["full"]),
    "table": ("table_checker", "ai", "检查文档中的表格是否规范完整", ["full"]),
    "cover": ("cover_checker", "ai", "检查封面等级信息是否规范", ["cover"]),
    "seal": ("rule_seal_checker", "rule", "检查公章:红色公章+公章名称与供应商一致", ["tail"]),
    "signer": ("rule_signer_checker", "rule", "检查校对人与批复人不得为同一人", ["tail", "cover"]),
}


# --------------------------------------------------------------------------
# 章节提取:把 OCR 文档(markdown)拆成 {章节名: 内容}
# --------------------------------------------------------------------------
def extract_sections(doc_content: str) -> dict[str, str]:
    """从 OCR 文档内容提取章节。

    拆分规则:
    - cover:第一个标题(# / ## ...)之前的内容(封面/抬头)
    - 按标题拆:每个标题到下一个标题之间为该标题的章节,key=标题文字
    - tail:最后一个标题章节的末尾部分(签字盖章区,取最后 ~800 字符)

    返回 dict,含 "cover"、"tail" 及各标题名。
    """
    sections: dict[str, str] = {}
    lines = doc_content.split("\n")

    # 找所有标题行(markdown # 开头,或中文编号「一、」「1.」开头的大标题)
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
    # 标记每段的起始行
    segments: list[tuple[str, list[str]]] = []  # (标题名, 该段行)
    current_title = "cover"
    current_lines: list[str] = []

    for line in lines:
        m = heading_re.match(line)
        if m:
            # 遇到新标题,保存前一段
            if current_lines:
                segments.append((current_title, current_lines))
            current_title = m.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    # 最后一段
    if current_lines:
        segments.append((current_title, current_lines))

    # 组装 sections
    for title, seg_lines in segments:
        text = "\n".join(seg_lines).strip()
        if title == "cover":
            sections["cover"] = text
        else:
            sections[title] = text

    # tail:取全文最后 ~800 字符(签字盖章区通常在末尾)
    if len(doc_content) > 800:
        sections["tail"] = doc_content[-800:]
    elif segments:
        sections["tail"] = "\n".join(segments[-1][1]).strip()

    return sections


def pick_sections(sections: dict[str, str], wanted: list[str]) -> str:
    """根据检查项声明的 wanted 章节,拼出对应的文档内容。

    wanted 含 "full" 时返回全文(不按章节)。
    否则按章节名模糊匹配(标题含关键词即取),找不到的章节跳过。
    """
    if "full" in wanted or not wanted:
        return "\n\n".join(sections.values())

    parts: list[str] = []
    for w in wanted:
        if w in sections:
            parts.append(f"### {w}\n{sections[w]}")
            continue
        # 模糊匹配:章节 key 包含 wanted 关键词
        matched = [v for k, v in sections.items() if w in k]
        for k, v in sections.items():
            if w in k and f"### {k}\n{v}" not in parts:
                parts.append(f"### {k}\n{v}")
        if not matched and w not in ("cover", "tail"):
            log.debug("章节 '%s' 未找到,跳过", w)
    return "\n\n".join(parts) if parts else "\n\n".join(sections.values())


def build_check_prompt(section_content: str, entity: dict, task: str) -> str:
    """把「该检查需要的章节内容」+ entity 业务数据 + 检查任务拼成检查 agent 的输入。

    section_content 由 pick_sections 按检查项声明的章节筛选得出(不全传全文)。
    entity 作为比对基准(供应商名、人员等)明确告知 agent。
    """
    entity_text = json.dumps(entity, ensure_ascii=False, indent=2) if entity else "(无)"
    return (
        f"## 检查任务\n{task}\n\n"
        f"## 业务数据(比对基准)\n{entity_text}\n\n"
        f"## 文档内容(对应章节)\n{section_content}"
    )


async def run_review(
    entity: dict[str, Any],
    url: str,
    review_id: str | None = None,
    callback_url: str | None = None,
) -> dict[str, Any]:
    """执行完整文档审核流程,返回结构化报告。

    Args:
        entity:       业务数据(供应商、人员、项目等,比对基准)
        url:          待审核文件的 URL
        review_id:    审核任务 id(不传则自动生成)
        callback_url: 审核完成后的回调地址(可选)

    Returns:
        结构化审核报告 dict
    """
    from app.ai.gateway import agent_gateway

    review_id = review_id or uuid.uuid4().hex
    check_timeout = settings.doc_review.check_timeout
    log.info("文档审核开始 review_id=%s url=%s entity_keys=%s", review_id, url, list(entity))

    # ① OCR + 排序恢复
    log.info("[%s] 步骤1:OCR + 排序", review_id)
    try:
        ocr_result = await asyncio.wait_for(
            agent_gateway.trigger("doc_ocr_sorter", url, source="api"),
            timeout=check_timeout,
        )
        doc_content = ocr_result.output
        log.info("[%s] OCR 完成,文档 %d 字符", review_id, len(doc_content))
    except Exception as e:
        log.exception("[%s] OCR 失败", review_id)
        return {
            "review_id": review_id,
            "status": "failed",
            "error": f"OCR 失败: {e}",
        }

    # ② 提取章节(按标题自动拆 + 封面/末尾区域)
    log.info("[%s] 步骤2:提取章节", review_id)
    sections = extract_sections(doc_content)
    log.info("[%s] 章节提取完成: %s", review_id, list(sections))

    # ③ 并行检查:每项只拿「所需章节 + entity」(不全传全文)
    log.info("[%s] 步骤3:并行检查(%d 项)", review_id, len(CHECKS))
    check_tasks = {}
    for check_name, (agent_name, check_type, task_desc, req_sections) in CHECKS.items():
        section_content = pick_sections(sections, req_sections)
        prompt = build_check_prompt(section_content, entity, task_desc)
        check_tasks[check_name] = (agent_name, check_type, prompt)

    async def _run_one(name: str, agent: str, prompt: str) -> tuple[str, str]:
        try:
            r = await asyncio.wait_for(
                agent_gateway.trigger(agent, prompt, source="api"),
                timeout=check_timeout,
            )
            return name, r.output
        except Exception as e:
            log.exception("[%s] 检查项 '%s' 失败", review_id, name)
            return name, json.dumps({"pass": False, "issues": [f"检查异常: {e}"]})

    results = await asyncio.gather(
        *[_run_one(n, a, p) for n, (a, _, p) in check_tasks.items()]
    )
    check_results = {name: output for name, output in results}
    log.info("[%s] 并行检查完成", review_id)

    # ④ 汇总报告
    log.info("[%s] 步骤4:汇总报告", review_id)
    all_results_text = "\n\n".join(
        f"### {name} (类型: {check_tasks[name][1]})\n{output}"
        for name, output in check_results.items()
    )
    report_prompt = (
        f"## 审核任务汇总\n请把以下各项检查结果整合成审核报告。\n\n"
        f"## 业务数据\n{json.dumps(entity, ensure_ascii=False)}\n\n"
        f"## 各项检查结果\n{all_results_text}"
    )
    try:
        report_result = await asyncio.wait_for(
            agent_gateway.trigger("review_reporter", report_prompt, source="api"),
            timeout=check_timeout,
        )
        report_text = report_result.output
    except Exception as e:
        log.exception("[%s] 汇总报告失败", review_id)
        report_text = json.dumps(
            {"overall_pass": False, "error": f"汇总失败: {e}", "checks": check_results},
            ensure_ascii=False,
        )

    # 解析报告(尝试转 JSON;失败则原文返回)
    report = _parse_report(report_text)
    report["review_id"] = review_id
    report["entity"] = entity
    report["status"] = "completed"

    # ④ 回调(若提供)
    if callback_url:
        await _callback(callback_url, report, review_id)

    log.info("[%s] 审核完成 overall_pass=%s", review_id, report.get("overall_pass"))
    return report


async def _callback(url: str, report: dict, review_id: str) -> None:
    """审核完成后把结果 POST 回外部系统。失败只记日志不影响主流程。"""
    try:
        from app.services.http_client import http_client

        await http_client.post(
            url, json=report, timeout=settings.doc_review.callback_timeout
        )
        log.info("[%s] 回调成功: %s", review_id, url)
    except Exception:
        log.exception("[%s] 回调失败: %s", review_id, url)


def _parse_report(text: str) -> dict[str, Any]:
    """尝试把报告文本解析成 JSON;失败则包一层返回。"""
    import re

    # 提取 JSON(容忍 ```json 代码块和前后文字)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": text, "overall_pass": None}


__all__ = ["run_review", "build_check_prompt", "CHECKS"]
