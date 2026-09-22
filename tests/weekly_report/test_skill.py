"""C1 — Skill 渲染管线测试。

不依赖任何 LLM / 网络（设计 §21 / 任务说明「无 LLM 也应可生成骨架」）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.projects.weekly_report.domain.models import ReportWindow
from app.skills.weekly_report.skill import (
    ReportSpec,
    build_default_skill,
)


def _spec(report_type: str, window: ReportWindow) -> ReportSpec:
    return ReportSpec(
        report_type=report_type,
        period=window.period_iso(),
        window_start=window.window_start,
        window_end=window.window_end,
        generated_at=datetime(2026, 5, 4, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def window() -> ReportWindow:
    return ReportWindow(
        window_start=datetime(2026, 4, 27, tzinfo=UTC),
        window_end=datetime(2026, 5, 4, tzinfo=UTC),
    )


def test_default_skill_renders_cps_issue_inspection_html(window: ReportWindow):
    skill = build_default_skill()
    snapshot = {
        "period": window.period_iso(),
        "window_start": window.window_start.isoformat(),
        "window_end": window.window_end.isoformat(),
        "stats": {
            "total": 42,
            "completed": 36,
            "pending": 6,
            "pass_rate": 0.75,
            "rectify_rate": 0.83,
        },
        "top_categories": [["设备接地", 8], ["消防通道", 5]],
    }
    rendered = skill.render(_spec("cps-issue-inspection", window), snapshot)
    html = rendered.body.decode("utf-8")
    assert "<!doctype html>" in html.lower()
    assert "设备接地" in html
    assert "42" in html  # stats.total
    assert rendered.template_id == "cps-issue-inspection"


def test_default_skill_renders_room_check_html(window: ReportWindow):
    skill = build_default_skill()
    snapshot = {
        "period": window.period_iso(),
        "stats": {"total": 18, "avg_score": 92.4},
        "low_score_rooms": [["ROOM-A03", 71]],
    }
    rendered = skill.render(_spec("room-check", window), snapshot)
    html = rendered.body.decode("utf-8")
    assert "ROOM-A03" in html
    assert "92.4" in html


def test_default_skill_falls_back_to_generic_when_unknown_type(window: ReportWindow):
    skill = build_default_skill()
    rendered = skill.render(
        _spec("some-future-type", window),
        {"period": window.period_iso(), "note": "未注册数据快照"},
    )
    html = rendered.body.decode("utf-8")
    assert "<!doctype html>" in html.lower()
    assert rendered.template_id == "generic"
    assert "未注册数据快照" in html or "some-future-type" in html


def test_skill_render_is_deterministic(window: ReportWindow):
    skill = build_default_skill()
    snapshot = {"period": window.period_iso(), "stats": {"total": 1}}
    a = skill.render(_spec("room-check", window), snapshot)
    b = skill.render(_spec("room-check", window), snapshot)
    assert a.skill_version == b.skill_version
    assert a.template_id == b.template_id
    # 同一输入 → 主表内容一致
    assert a.body == b.body


def test_compute_window_contains_semantics():
    """window.contains 满足 [start, end) 含头不含尾（AC-28）。"""
    from zoneinfo import ZoneInfo

    from app.projects.weekly_report.application.service import compute_window

    win = compute_window(now=datetime(2026, 5, 4, 8, 0, tzinfo=UTC))
    # 业务口径：Asia/Shanghai 周一 00:00（设计 §3.3）
    cn_start = win.window_start.astimezone(ZoneInfo("Asia/Shanghai"))
    cn_end = win.window_end.astimezone(ZoneInfo("Asia/Shanghai"))
    assert cn_start.isoweekday() == 1
    assert cn_start.hour == 0 and cn_start.minute == 0
    assert cn_end.hour == 0 and cn_end.minute == 0
    delta = win.window_end - win.window_start
    assert delta == timedelta(days=7)
    assert win.contains(win.window_start)
    assert win.contains(win.window_end - timedelta(seconds=1))
    assert not win.contains(win.window_end)


def test_period_iso_format():
    win = ReportWindow(
        window_start=datetime(2026, 1, 5, tzinfo=UTC),  # ISO 2026-W02
        window_end=datetime(2026, 1, 12, tzinfo=UTC),
    )
    assert win.period_iso() == "2026-W02"


__all__ = []