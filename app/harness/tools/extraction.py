"""结构化提取工具 —— 把 OCR markdown 解析成统一 schema（PRD §5）。

这部分工具做**确定性**的 markdown 解析，供提取 agent 调用；agent 负责编排
与 LLM 归一（标题错字、表格错行等）。工具不含 LLM 调用，纯代码、快且准。

当前提供：
- extract_markdown_tables：把 markdown 表格文本解析成 [{表头, rows}]。
- extract_work_checks：从文本里识别「作业类型 + √/×/勾选/空白」清单。
- extract_kv_lines：从封面/签字区文本解析 "键: 值" 键值行。
- normalize_heading：标题归一（去序号花式写法，如 5)/九:/10、）。

返回值都是 JSON 字符串，agent 可进一步加工。
"""

from __future__ import annotations

import json
import re

from app.harness.tools import tool


@tool("extract_markdown_tables")
async def extract_markdown_tables(md: str) -> str:
    """从 OCR markdown 中解析所有表格，返回 JSON 数组。

    每个表格形如 {"title": 表格前的标题或空, "headers": [...], "rows": [[...]]}。
    解析 markdown 管道表格(| a | b |)；次要去除分隔行(| --- |)。

    Args:
        md: OCR 出的 markdown 全文（可含多张表格与文字）。
    """
    tables: list[dict] = []
    lines = (md or "").splitlines()
    i = 0
    pending_title = ""
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            pending_title = line.lstrip("#").strip()
            i += 1
            continue
        if _is_table_row(line):
            header_cells = _split_row(line)
            i += 1
            if i < len(lines) and _is_separator(lines[i].strip()):
                i += 1
            rows: list[list[str]] = []
            while i < len(lines) and _is_table_row(lines[i].strip()):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            tables.append(
                {
                    "title": pending_title,
                    "headers": header_cells,
                    "rows": rows,
                }
            )
            pending_title = ""
            continue
        i += 1
    return json.dumps({"tables": tables}, ensure_ascii=False)


@tool("extract_work_checks")
async def extract_work_checks(text: str, work_types_csv: str) -> str:
    """从文本识别各作业类型的四态勾选结果。

    符号会分配给前后距离最近的作业名称，避免把下一个选项的符号误算到当前
    选项。CHECKED=明确勾选，UNCHECKED=明确打叉，BLANK=明确空框，UNKNOWN=
    名称缺失、没有相邻符号、符号冲突或无法判断。

    Args:
        text: 封面/交底书里含作业类型勾选区域的文本。
        work_types_csv: 候选作业类型，逗号分隔（如 动火作业,高处作业,...）。
    """
    text = text or ""
    work_types = [wt.strip() for wt in re.split(r"[,，]", work_types_csv) if wt.strip()]
    states = _extract_work_states(text, work_types)
    result = {
        "checked": [work_type for work_type in work_types if states[work_type] == "CHECKED"],
        "unchecked": [work_type for work_type in work_types if states[work_type] == "UNCHECKED"],
        "blank": [work_type for work_type in work_types if states[work_type] == "BLANK"],
        "unknown": [work_type for work_type in work_types if states[work_type] == "UNKNOWN"],
    }
    return json.dumps(result, ensure_ascii=False)


@tool("extract_kv_lines")
async def extract_kv_lines(text: str) -> str:
    """从文本解析 "键: 值" 键值行，返回 JSON 对象。

    Args:
        text: 封面/签字区/信息栏文本。
    """
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r"^([^:：]+)\s*[:：]\s*(.*)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key and val and key not in out:
                out[key] = val
    return json.dumps(out, ensure_ascii=False)


@tool("normalize_heading")
async def normalize_heading(title: str) -> str:
    """标题归一：去掉中文/数字序号的花式写法，返回标准中文标题。

    处理 5)/九:/10、  一、工程概况  →工程概况；用于章节匹配容忍 OCR 错字。

    Args:
        title: 表单/OCR 里的原始标题。
    """
    t = (title or "").strip()
    t = re.sub(r"^[一二三四五六七八九十百]+[、.．:：)）/\-]\s*", "", t)
    t = re.sub(r"^(\d{1,2})[、.．:：)）/\-]\s*", "", t)
    t = re.sub(r"^[（(]\s*[一二三四五六七八九十\d]+\s*[)）]\s*", "", t)
    return json.dumps({"normalized": t}, ensure_ascii=False)


# --------------------------------------------------------------------------
# 内部解析 helpers
# --------------------------------------------------------------------------
def _is_table_row(line: str) -> bool:
    return line.startswith("|") and "|" in line[1:]


def _is_separator(line: str) -> bool:
    # markdown 分隔行: | --- | --- |
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line))


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


_MARK_STATES = {
    "√": "CHECKED",
    "✓": "CHECKED",
    "✔": "CHECKED",
    "☑": "CHECKED",
    "☒": "CHECKED",
    "×": "UNCHECKED",
    "✕": "UNCHECKED",
    "✖": "UNCHECKED",
    "☐": "BLANK",
    "□": "BLANK",
}
_MARK_SEPARATORS = re.compile(r"[\s:：,，;；()（）\[\]【】._\-/]*")


def _extract_work_states(text: str, work_types: list[str]) -> dict[str, str]:
    occurrences: list[tuple[int, int, str, int]] = []
    for order, work_type in enumerate(work_types):
        occurrences.extend(
            (match.start(), match.end(), work_type, order)
            for match in re.finditer(re.escape(work_type), text)
        )

    assigned: dict[str, list[str]] = {work_type: [] for work_type in work_types}
    mark_pattern = "[" + re.escape("".join(_MARK_STATES)) + "]"
    for mark in re.finditer(mark_pattern, text):
        candidates: list[tuple[int, int, int, str]] = []
        for start, end, work_type, order in occurrences:
            if end <= mark.start():
                separator = text[end : mark.start()]
                side_priority = 1  # equal distance prefers a mark before the next label
            elif mark.end() <= start:
                separator = text[mark.end() : start]
                side_priority = 0
            else:
                continue
            if len(separator) <= 6 and _MARK_SEPARATORS.fullmatch(separator):
                candidates.append((len(separator), side_priority, order, work_type))
        if candidates:
            candidates.sort()
            nearest = [item for item in candidates if item[0] == candidates[0][0]]
            if len({item[3] for item in nearest}) > 1:
                continue
            work_type = candidates[0][3]
            assigned[work_type].append(_MARK_STATES[mark.group()])

    states: dict[str, str] = {}
    for work_type in work_types:
        observed = set(assigned[work_type])
        if len(observed) == 1:
            states[work_type] = observed.pop()
        else:
            # A visible label without an explicit box is still unknown, not unchecked.
            states[work_type] = "UNKNOWN"
    return states


__all__ = [
    "extract_markdown_tables",
    "extract_work_checks",
    "extract_kv_lines",
    "normalize_heading",
]
