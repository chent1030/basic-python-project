"""周报 HTML 模板（按 §21.2 报告分类配置驱动）。

每个分类的模板定义：
- ``title``：报告标题（占位 ``{type_display}`` 会被替换为分类中文名）；
- ``sections``：section 列表（``id`` / ``title`` / ``render(data)``）；
- ``render(data)``：把 ``ReportData`` 拼成 HTML 片段（默认实现 deterministic）。

模板是 dict 形态（无外部 Jinja2 依赖 — 满足「无 LLM 也可生成骨架」基线）。
后续可平滑替换为 Jinja2 / 其它实现，只要返回字符串 bytes 即可。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Any

# 类型别名：data → HTML 片段
SectionRenderer = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    render: SectionRenderer


@dataclass(frozen=True)
class ReportTemplate:
    """一种报告类型的模板定义。"""

    type_id: str  # 与 WeeklyReportRun.report_type 对齐（如 "cps-issue-inspection"）
    type_display: str  # 中文显示名（"问题巡检"）
    title: str  # HTML head 标题（可含 ``{type_display}``）
    sections: tuple[Section, ...]
    #: 报告头部说明（HTML 片段：含周期、数据时间、生成时间等）
    header_render: SectionRenderer

    def render(self, data: dict[str, Any]) -> str:
        """拼装完整 HTML（无外部依赖；骨架确定版，对齐「无 LLM 也可生成」）。"""
        body_parts: list[str] = []
        body_parts.append(self.header_render(data))
        for section in self.sections:
            body_parts.append(
                f'<section id="{escape(section.id)}">'
                f'<h2>{escape(section.title)}</h2>'
                f'{section.render(data)}'
                f'</section>'
            )
        body = "\n".join(body_parts)
        head_title = self.title.format(type_display=escape(self.type_display))
        return (
            "<!doctype html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            f"  <meta charset=\"utf-8\">\n"
            f"  <title>{head_title}</title>\n"
            "  <style>"
            "body{font-family:-apple-system,Segoe UI,sans-serif;margin:2rem auto;"
            "max-width:960px;color:#1f2937;line-height:1.6}"
            "h1{font-size:1.5rem;border-bottom:2px solid #2563eb;padding-bottom:.5rem}"
            "h2{font-size:1.15rem;margin-top:1.5rem;color:#2563eb}"
            "table{border-collapse:collapse;width:100%;margin:.5rem 0}"
            "th,td{border:1px solid #e5e7eb;padding:.4rem .6rem;text-align:left}"
            "th{background:#f3f4f6}"
            ".meta{color:#6b7280;font-size:.9rem;margin-bottom:1rem}"
            "</style>\n"
            "</head>\n"
            "<body>\n"
            f"<h1>{head_title}</h1>\n"
            f"{body}\n"
            "</body>\n"
            "</html>\n"
        )


def _render_header(data: dict[str, Any]) -> str:
    """报告头部：周期 / 窗口 / 生成时间。"""
    period = escape(str(data.get("period", "")))
    window_start = escape(str(data.get("window_start", "")))
    window_end = escape(str(data.get("window_end", "")))
    generated_at = escape(str(data.get("generated_at", "")))
    return (
        '<div class="meta">'
        f'<div>报告周期：{period}</div>'
        f'<div>窗口（含头不含尾）：{window_start} ~ {window_end}</div>'
        f'<div>生成时间：{generated_at}</div>'
        "</div>"
    )


def _render_kv_table(rows: list[tuple[str, Any]]) -> str:
    body = "\n".join(
        f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>"
        for k, v in rows
    )
    return f"<table>{body}</table>"


def _cps_issue_summary(data: dict[str, Any]) -> str:
    """问题巡检周报：总数 / 通过率 / 整改率 / 异常分类 Top-N。"""
    stats = data.get("stats") or {}
    rows: list[tuple[str, Any]] = [
        ("本期提交数量", stats.get("total", 0)),
        ("已完成", stats.get("completed", 0)),
        ("待审核", stats.get("pending", 0)),
        ("通过率", f"{stats.get('pass_rate', 0):.1%}"),
        ("整改率", f"{stats.get('rectify_rate', 0):.1%}"),
    ]
    return _render_kv_table(rows)


def _cps_issue_top(data: dict[str, Any]) -> str:
    items = data.get("top_categories") or []
    if not items:
        return "<p><em>无异常分类数据。</em></p>"
    rows = "\n".join(
        f"<tr><td>{escape(str(name))}</td><td>{count}</td></tr>"
        for name, count in items
    )
    return f"<table><tr><th>异常分类</th><th>数量</th></tr>{rows}</table>"


def _room_check_summary(data: dict[str, Any]) -> str:
    stats = data.get("stats") or {}
    rows: list[tuple[str, Any]] = [
        ("本期点检单数量", stats.get("total", 0)),
        ("已提交数量", stats.get("submitted", 0)),
        ("平均得分", f"{stats.get('avg_score', 0):.1f}"),
        ("满分单数量", stats.get("full_score", 0)),
    ]
    return _render_kv_table(rows)


def _room_check_low_score(data: dict[str, Any]) -> str:
    items = data.get("low_score_rooms") or []
    if not items:
        return "<p><em>本期无低分房间。</em></p>"
    rows = "\n".join(
        f"<tr><td>{escape(str(room_id))}</td><td>{score}</td></tr>"
        for room_id, score in items
    )
    return f"<table><tr><th>房间号</th><th>得分</th></tr>{rows}</table>"


def _generic_placeholder(data: dict[str, Any]) -> str:
    """未登记分类模板时的占位 section。"""
    return (
        "<p><em>该分类尚未注册定制模板，输出当前数据快照：</em></p>"
        f"<pre>{escape(str(data))}</pre>"
    )


# ---------------------------------------------------------------------------
# 注册表（§27.4 待确认：先落 CPS 问题巡检、辅房点检两类已知）
# ---------------------------------------------------------------------------
CPS_ISSUE_INSPECTION = ReportTemplate(
    type_id="cps-issue-inspection",
    type_display="问题巡检",
    title="{type_display} 周报",
    header_render=_render_header,
    sections=(
        Section(id="summary", title="总体情况", render=_cps_issue_summary),
        Section(id="top", title="异常分类 Top-N", render=_cps_issue_top),
    ),
)

ROOM_CHECK = ReportTemplate(
    type_id="room-check",
    type_display="辅房点检",
    title="{type_display} 周报",
    header_render=_render_header,
    sections=(
        Section(id="summary", title="总体情况", render=_room_check_summary),
        Section(
            id="low_score",
            title="低分房间",
            render=_room_check_low_score,
        ),
    ),
)


REPORT_TEMPLATES: dict[str, ReportTemplate] = {
    tpl.type_id: tpl
    for tpl in (CPS_ISSUE_INSPECTION, ROOM_CHECK)
}


def render_template(template: ReportTemplate, data: dict[str, Any]) -> str:
    """按模板拼装 HTML 字符串（确定性，不依赖 LLM）。"""
    return template.render(data)


__all__ = [
    "CPS_ISSUE_INSPECTION",
    "ROOM_CHECK",
    "REPORT_TEMPLATES",
    "ReportTemplate",
    "Section",
    "SectionRenderer",
    "render_template",
]