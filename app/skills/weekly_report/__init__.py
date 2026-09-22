"""周报 Skill（C1 周报 — §21 / 设计 §3.3）。

提供 deterministic 默认实现 ``WeeklyReportSkill``：根据 ``ReportSpec`` 与
``ReportData`` 拼装 HTML 内容。无外部模型依赖、无 LangGraph 依赖；后续可
在不改调用方的前提下替换为基于 LLM 的实现。

公开入口：
- :func:`build_default_skill` —— 装配默认 Skill（生产路径）；
- :class:`WeeklyReportSkill`（Protocol） —— 抽象接口；
- :class:`DefaultWeeklyReportSkill` —— 当前 deterministic 实现。
"""
from app.skills.weekly_report.skill import (
    DefaultWeeklyReportSkill,
    WeeklyReportSkill,
    build_default_skill,
)
from app.skills.weekly_report.templates import (
    REPORT_TEMPLATES,
    ReportTemplate,
    render_template,
)

__all__ = [
    "DefaultWeeklyReportSkill",
    "REPORT_TEMPLATES",
    "ReportTemplate",
    "WeeklyReportSkill",
    "build_default_skill",
    "render_template",
]