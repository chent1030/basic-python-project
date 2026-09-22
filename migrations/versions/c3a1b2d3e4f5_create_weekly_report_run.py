"""create weekly_report_run table

Revision ID: c3a1b2d3e4f5
Revises: b41c7f2ae903
Create Date: 2026-08-01 09:00:00.000000

周报归档运行记录表（设计 §3.3 / §4 / §21；AC-04/05/28/29）：

- 五态：PENDING / RUNNING / ARCHIVING / COMPLETED / FAILED；
- 独立 push_status 列（PENDING / SUCCESS / FAILED / SKIPPED / UNCONFIGURED）
  与 status 解耦，对齐 AC-29 / 设计 §3.3「上传成功≠推送成功」；
- UNIQUE(report_type, window_start, window_end) 兜底同窗口幂等（重跑生成
  新 run_no，对齐设计 §4 路径版本化）；
- Index(status, window_end) 支撑启动补跑扫描；
- Index(report_type, period) 支撑 C-05 列表按类型+周期过滤。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3a1b2d3e4f5"
down_revision: str | Sequence[str] | None = "b41c7f2ae903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "weekly_report_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("report_type", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("archive_object_key", sa.String(length=512), nullable=True),
        sa.Column("archive_bytes", sa.Integer(), nullable=True),
        sa.Column("archive_etag", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column(
            "report_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "push_status", sa.String(length=24), nullable=False, server_default="PENDING"
        ),
        sa.Column("push_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("push_result", sa.Text(), nullable=True),
        sa.Column("push_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_weekly_report_run_run_id"),
        sa.UniqueConstraint(
            "report_type",
            "window_start",
            "window_end",
            name="uq_weekly_report_run_type_window",
        ),
    )
    op.create_index(
        "ix_weekly_report_run_status_period",
        "weekly_report_run",
        ["status", "window_end"],
    )
    op.create_index(
        "ix_weekly_report_run_report_type",
        "weekly_report_run",
        ["report_type", "period"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_weekly_report_run_report_type", table_name="weekly_report_run")
    op.drop_index("ix_weekly_report_run_status_period", table_name="weekly_report_run")
    op.drop_table("weekly_report_run")