"""周报归档运行记录表 ORM（设计 §3.3 / §4 / §21；AC-04/05/28/29）。

五态：PENDING / RUNNING / ARCHIVING / COMPLETED / FAILED。
- PENDING  ：行刚创建（数据库唯一约束兜底同窗口幂等）；
- RUNNING  ：已锁定、正在生成报告内容；
- ARCHIVING：已生成 HTML、上传 RustFS 中；
- COMPLETED：归档成功，进入推送流程（push_status 决定推送侧最终语义）；
- FAILED   ：生成/归档失败，含 error_code 与中文 error 文案（Java 侧运维查询友好）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: JSON 列：PG 用 JSONB，SQLite（测试）退化为通用 JSON。
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WeeklyReportRun(Base):
    """一次周报任务的运行记录与归档状态。

    幂等键：``(report_type, window_start, window_end)`` UNIQUE——同窗口重跑会
    生成新 ``run_no``（不覆盖历史归档，对齐设计 §3.3 重跑策略）。
    调度/启动补跑：以同 (type, window_start) 找到既有 COMPLETED 行 → 跳过；
    没有 → 新行 RUNNING；INSERT 撞唯一索引 → 重取既有行判定。
    """

    __tablename__ = "weekly_report_run"
    __table_args__ = (
        UniqueConstraint(
            "report_type", "window_start", "window_end",
            name="uq_weekly_report_run_type_window",
        ),
        Index("ix_weekly_report_run_status_period", "status", "window_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 公开 run_id（uuid4 字符串，外部 API/资源路径都用这个）
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: 周报分类（"cps-issue-inspection" / "room-check" / 后续 §27.4 配置驱动扩展）
    report_type: Mapped[str] = mapped_column(String(64), index=True)
    #: ISO 周编号（yyyy-Ww），归档路径段
    period: Mapped[str] = mapped_column(String(16), index=True)
    #: 窗口起点（UTC，tz-aware）：含头（设计 §3.3 / AC-28 左闭）
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: 窗口终点（UTC，tz-aware）：不含尾（左闭右开 [start, end)）
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: 同窗口重跑计数器（run_no = 1 起，每次重试 +1，对齐设计 §4 路径版本化）
    run_no: Mapped[int] = mapped_column(Integer, default=1)
    #: 重试次数（≤3 上限留给未来 backoff 逻辑使用，结构先行）
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # -------- 五态（见模块 docstring） --------
    status: Mapped[str] = mapped_column(String(24), default="PENDING")

    # -------- 归档输出 --------
    archive_object_key: Mapped[str | None] = mapped_column(String(512))
    archive_bytes: Mapped[int | None] = mapped_column(Integer)
    archive_etag: Mapped[str | None] = mapped_column(String(64))

    # -------- 时间戳 --------
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 渲染所用 Skill/数据 fetcher 版本（model_version 组合串）
    model_version: Mapped[str | None] = mapped_column(String(128))
    #: 生成期缓存的报告骨架 + 数据快照（生成/重跑定位时回看；不带 base64 附件）
    report_snapshot: Mapped[dict | None] = mapped_column(JSONVariant)

    # -------- 错误 --------
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))

    # -------- 推送独立列（与 status 解耦，对齐 AC-29 / 设计 §3.3） --------
    #: PENDING / SUCCESS / FAILED / SKIPPED / UNCONFIGURED
    push_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    push_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    push_result: Mapped[str | None] = mapped_column(Text)
    push_attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


__all__ = ["WeeklyReportRun"]