"""周报 Skill 接口（C1 — §21 / 设计 §3.3）。

抽象：``WeeklyReportSkill`` = Protocol（render(spec, data) → bytes）。
默认实现：``DefaultWeeklyReportSkill``（deterministic 模板拼装，无 LLM）。
后续可替换为基于 LangGraph / LLM 的实现，只要满足 Protocol 即可。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.skills.weekly_report.templates import (
    REPORT_TEMPLATES,
    ReportTemplate,
    render_template,
)


@dataclass(frozen=True)
class ReportSpec:
    """调用方（agent / service）传入的渲染规约。"""

    report_type: str
    period: str  # "yyyy-Ww"
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    #: Skill/模板版本（入 WeeklyReportRun.model_version）
    skill_version: str = "weekly-report-skill@1"


@dataclass(frozen=True)
class RenderedReport:
    """Skill 渲染产物。"""

    content_type: str  # "text/html; charset=utf-8"
    body: bytes  # 报告 HTML 字节流
    skill_version: str
    template_id: str  # 命中的 template.type_id（未命中 = "generic"）


@runtime_checkable
class WeeklyReportSkill(Protocol):
    """周报渲染 Skill 抽象接口。

    调用方 ``application/service.py`` 只依赖本 Protocol，便于测试与未来替换。
    """

    def render(self, spec: ReportSpec, data: dict[str, Any]) -> RenderedReport:
        """按 spec + data 渲染报告字节流。无 LLM 也应可生成（骨架确定性）。"""
        ...


class DefaultWeeklyReportSkill:
    """基于模板表的 deterministic 默认实现。

    - ``report_type`` 命中已注册模板 → 走对应模板；
    - 未命中 → 退化为通用占位（HTML 包含数据快照，§21 行增加可扩展）。
    """

    def __init__(
        self,
        *,
        templates: dict[str, ReportTemplate] | None = None,
        skill_version: str = "weekly-report-skill@1",
    ) -> None:
        self._templates = dict(templates or REPORT_TEMPLATES)
        self._skill_version = skill_version

    def render(self, spec: ReportSpec, data: dict[str, Any]) -> RenderedReport:
        template = self._templates.get(spec.report_type)
        if template is None:
            html = _fallback_html(spec, data)
            return RenderedReport(
                content_type="text/html; charset=utf-8",
                body=html.encode("utf-8"),
                skill_version=self._skill_version,
                template_id="generic",
            )
        html = render_template(template, data)
        return RenderedReport(
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
            skill_version=self._skill_version,
            template_id=template.type_id,
        )


def _fallback_html(spec: ReportSpec, data: dict[str, Any]) -> str:
    """未注册分类的通用 HTML 骨架。"""
    from html import escape

    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\"><title>"
        f"{escape(spec.report_type)} 周报（无定制模板）"
        "</title></head><body>"
        f"<h1>{escape(spec.report_type)} 周报</h1>"
        f"<p>周期：{escape(spec.period)}</p>"
        f"<p>窗口：{escape(spec.window_start.isoformat())} ~ "
        f"{escape(spec.window_end.isoformat())}</p>"
        f"<p>生成时间：{escape(spec.generated_at.isoformat())}</p>"
        f"<h2>数据快照</h2><pre>{escape(str(data))}</pre>"
        "</body></html>"
    )


def build_default_skill() -> WeeklyReportSkill:
    """装配默认 Skill（生产路径入口；测试可直接 new DefaultWeeklyReportSkill）。"""
    return DefaultWeeklyReportSkill()


__all__ = [
    "DefaultWeeklyReportSkill",
    "RenderedReport",
    "ReportSpec",
    "WeeklyReportSkill",
    "build_default_skill",
]