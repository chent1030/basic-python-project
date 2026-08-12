"""Harness 基类:BaseCheck(检查项) + BaseStage(流水线阶段)。

检查项:一文件一个,继承 BaseCheck,自动发现。声明需要哪些章节,run 里做判断。
阶段:Pipeline 的一个步骤,读写 Context。
"""
from __future__ import annotations

import abc
import re

from app.harness.context import CheckResult, HarnessContext


class BaseCheck(abc.ABC):
    """所有检查项的基类。一个文件定义一个检查项,自动发现。

    子类需设:name / description / sections / check_type,并实现 run()。
    加检查项 = 加一个 .py 文件定义子类 + 模块级 CHECK = XxxCheck()。
    """

    name: str = ""
    description: str = ""
    sections: list[str] = []        # 需要哪些章节:"full"/"cover"/"tail"/标题关键词
    check_type: str = "ai"          # "ai"(调LLM) | "rule"(纯代码) | "hybrid"

    @abc.abstractmethod
    async def run(self, ctx: HarnessContext) -> CheckResult:
        """执行检查,返回 CheckResult。"""
        raise NotImplementedError

    def get_input(self, ctx: HarnessContext) -> str:
        """从 ctx 取本检查需要的章节内容(根据 self.sections)。

        sections 含 "full" 时返回全文;否则按章节名取(模糊匹配)。
        """
        if not ctx.sections:
            return ctx.ocr_text
        return pick_sections(ctx.sections, self.sections)


class BaseStage(abc.ABC):
    """Pipeline 的一个阶段。读写 Context。"""

    name: str = ""

    @abc.abstractmethod
    async def run(self, ctx: HarnessContext) -> HarnessContext:
        """执行本阶段,修改并返回 ctx。"""
        raise NotImplementedError


# --------------------------------------------------------------------------
# 章节提取/选取(从 OCR markdown 拆章节)
# --------------------------------------------------------------------------
def extract_sections(doc_content: str) -> dict[str, str]:
    """从 OCR 文档(markdown)提取章节。

    - cover:第一个标题前的内容(封面/抬头)
    - 按标题拆:每个 # 标题到下一个标题为一段
    - tail:末尾约 800 字符(签字盖章区)
    """
    sections: dict[str, str] = {}
    lines = doc_content.split("\n")
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

    current_title = "cover"
    current_lines: list[str] = []
    segments: list[tuple[str, list[str]]] = []

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_lines:
                segments.append((current_title, current_lines))
            current_title = m.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        segments.append((current_title, current_lines))

    for title, seg_lines in segments:
        text = "\n".join(seg_lines).strip()
        sections[title] = text

    # cover = 第一个标题前
    if segments and segments[0][0] == "cover":
        sections["cover"] = "\n".join(segments[0][1]).strip()

    # tail = 末尾
    if len(doc_content) > 800:
        sections["tail"] = doc_content[-800:]
    elif segments:
        sections["tail"] = "\n".join(segments[-1][1]).strip()

    return sections


def pick_sections(sections: dict[str, str], wanted: list[str]) -> str:
    """根据 wanted 章节列表取内容。

    "full" → 全文;否则按章节名模糊匹配。
    """
    if "full" in wanted or not wanted:
        return "\n\n".join(sections.values())

    parts: list[str] = []
    for w in wanted:
        if w in sections:
            parts.append(f"### {w}\n{sections[w]}")
            continue
        for k, v in sections.items():
            if w in k and f"### {k}\n{v}" not in parts:
                parts.append(f"### {k}\n{v}")
    return "\n\n".join(parts) if parts else "\n\n".join(sections.values())


__all__ = [
    "BaseCheck",
    "BaseStage",
    "extract_sections",
    "pick_sections",
]
