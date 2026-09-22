"""数据取数抽象（C2 — B7 数据契约占位）。

本期（B7 未到位）默认注入 ``StubDataFetcher``，返回稳定 stub dict；Service
层只依赖 ``WeeklyReportDataFetcher`` Protocol，B7 接口定稿后注入 Java 端
``JavaWeeklyReportDataFetcher``（下一波次实现）。
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.projects.weekly_report.domain.models import ReportWindow


@dataclass(frozen=True)
class FetchRequest:
    """数据取数请求（一次性快照：当前 report_type + window）。"""

    report_type: str
    window: ReportWindow


@runtime_checkable
class WeeklyReportDataFetcher(Protocol):
    """周报数据取数抽象接口。

    B7 到位后的实现：HTTP 调 Java 侧 ``/api/cps/weekly-report/data``，
    按 report_type 拉取窗口内业务数据 + 统计快照。
    """

    @abstractmethod
    async def fetch(self, request: FetchRequest) -> dict[str, Any]:
        """返回字典快照；键名由 §21.4 / B7 数据契约冻结（本波次 stub）。"""
        ...


class StubDataFetcher:
    """B7 未到位期间的稳定 stub（确定性、可重放、零随机性）。

    不同 report_type 返回不同形态，便于测试 + smoke 脚本验证渲染管线。
    """

    def __init__(self, *, now: datetime | None = None) -> None:
        self._now = now

    async def fetch(self, request: FetchRequest) -> dict[str, Any]:
        now = (self._now or datetime.now(UTC)).isoformat()
        period = request.window.period_iso()
        start = request.window.window_start.isoformat()
        end = request.window.window_end.isoformat()
        if request.report_type == "cps-issue-inspection":
            return {
                "period": period,
                "window_start": start,
                "window_end": end,
                "generated_at": now,
                "stats": {
                    "total": 42,
                    "completed": 36,
                    "pending": 6,
                    "pass_rate": 0.75,
                    "rectify_rate": 0.83,
                },
                "top_categories": [
                    ["设备接地", 8],
                    ["消防通道堵塞", 5],
                    ["防护用品过期", 3],
                ],
            }
        if request.report_type == "room-check":
            return {
                "period": period,
                "window_start": start,
                "window_end": end,
                "generated_at": now,
                "stats": {
                    "total": 18,
                    "submitted": 17,
                    "avg_score": 92.4,
                    "full_score": 12,
                },
                "low_score_rooms": [
                    ["ROOM-A03", 71],
                    ["ROOM-B07", 68],
                ],
            }
        # 未知分类：返回最小快照，模板表会降级为 generic HTML
        return {
            "period": period,
            "window_start": start,
            "window_end": end,
            "generated_at": now,
            "note": f"未注册数据快照：{request.report_type}",
        }


__all__ = [
    "FetchRequest",
    "StubDataFetcher",
    "WeeklyReportDataFetcher",
]