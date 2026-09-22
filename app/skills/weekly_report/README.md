# 周报 Skill（C1）

对齐 PRD §21「报告 Skill」与设计文档 §3.3 / §7.2（W8）。

## 接口

```python
from app.skills.weekly_report import (
    WeeklyReportSkill,        # Protocol
    DefaultWeeklyReportSkill, # 默认 deterministic 实现
    ReportSpec, RenderedReport,
    build_default_skill,
)
```

`WeeklyReportSkill.render(spec, data) -> RenderedReport` —— `RenderedReport`
含 `body: bytes`（HTML 字节流）+ `content_type` + `skill_version` +
`template_id`。调用方只依赖 `Protocol`，便于替换实现（如基于 LLM / LangGraph）。

## 当前默认实现

`DefaultWeeklyReportSkill` 基于 `templates.py` 中的模板表 deterministic 拼装 HTML：
- 命中 `report_type` 对应模板 → 按 `Section.render` 拼装；
- 未命中 → 退化通用 HTML（包含数据快照，便于排查）。

**无外部依赖**：模板是普通 `dataclass(Section.render=data -> str)`，无
Jinja2 / LLM / 远程调用。满足「无 LLM 也应可生成骨架」基线。

## 扩展：新分类

在 `templates.py` 的 `REPORT_TEMPLATES` 注册表里追加一项：

```python
NEW_TEMPLATE = ReportTemplate(
    type_id="my-new-type",
    type_display="新分类",
    title="{type_display} 周报",
    header_render=_render_header,
    sections=(Section(id="x", title="...", render=lambda data: "<p>...</p>"),),
)
REPORT_TEMPLATES[NEW_TEMPLATE.type_id] = NEW_TEMPLATE
```

调用方无需改动；`build_default_skill()` 启动时读到最新表。

## 替换为 LLM 实现

构造一个满足 `WeeklyReportSkill` Protocol 的类（duck-typed），注入到
`WeeklyReportService.__init__(skill=...)` 即可。Service 不感知实现细节。