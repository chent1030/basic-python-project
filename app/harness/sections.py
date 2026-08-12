"""章节提取 —— 从 OCR markdown 拆章节。"""
from __future__ import annotations

import re


def extract_sections(doc_content: str) -> dict[str, str]:
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
        sections[title] = "\n".join(seg_lines).strip()
    if segments and segments[0][0] == "cover":
        sections["cover"] = "\n".join(segments[0][1]).strip()
    if len(doc_content) > 800:
        sections["tail"] = doc_content[-800:]
    elif segments:
        sections["tail"] = "\n".join(segments[-1][1]).strip()
    return sections

def pick_sections(sections: dict[str, str], wanted: list[str]) -> str:
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

__all__ = ["extract_sections", "pick_sections"]
