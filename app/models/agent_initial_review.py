"""Agent 初审执行审计表 ORM（设计 §2.3 agent_initial_review_exec，Python 侧 PG）。

status 取值对齐 Java 侧 cps_initial_review_task.status ENUM 六态：
RUNNING / FAILED / TIMEOUT_OPEN / COMPLETED / TAKEN_OVER / LATE_RESULT。
Python 执行侧实际只会写 RUNNING / FAILED / COMPLETED：
- 超时 → FAILED + error_code=TIMEOUT（TIMEOUT_OPEN 是 Java 侧 wall-clock 10 分钟判定态）；
- TAKEN_OVER / LATE_RESULT 由 Java 侧任务状态机维护，Python 只读对齐。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: JSON 列：PG 用 JSONB，SQLite（测试）退化为通用 JSON。
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentInitialReviewExec(Base):
    """一次初审任务的执行审计（幂等键 task_id = cps-rectify-{issue_id}-v{version_no}）。"""

    __tablename__ = "agent_initial_review_exec"
    __table_args__ = (
        UniqueConstraint("issue_id", "version_no", name="uq_agent_initial_review_issue_version"),
        Index("ix_agent_initial_review_status_deadline", "status", "deadline_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 幂等键（= task_ref，设计 §3.2）；重复 C-01 命中此唯一索引
    task_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    issue_id: Mapped[str] = mapped_column(String(64), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    submission_id: Mapped[str | None] = mapped_column(String(64))

    #: 六态见模块 docstring
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    #: PASS / PARTIAL / PROBLEM（对齐 Java cps_initial_review_result.overall）
    overall: Mapped[str | None] = mapped_column(String(16))
    #: 本波次 = text-rules/d-22@1（确定性规则集版本）；后续视觉/语义模型各自登记
    model_version: Mapped[str | None] = mapped_column(String(128))

    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 任务级 wall-clock deadline：started_at + 480s（8 分钟，设计 §3.1:154）
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: 重放请求的参数指纹（同 task_id 异参重放 → 409）
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    #: 请求快照（三文本字段 + 附件元数据，base64 已剥离）
    input_snapshot: Mapped[dict | None] = mapped_column(JSONVariant)
    #: 确定性文本检查结果（L/P/违规明细，按字段）
    text_checks: Mapped[dict | None] = mapped_column(JSONVariant)
    #: 视觉/语义检查占位结果（NOT_IMPLEMENTED 项，待线 A6/A7/A8）
    model_checks: Mapped[dict | None] = mapped_column(JSONVariant)

    #: PENDING / SENT / FAILED
    callback_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    callback_last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
