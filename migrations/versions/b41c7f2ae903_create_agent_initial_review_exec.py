"""create agent_initial_review_exec table

Revision ID: b41c7f2ae903
Revises: 9d35bb4a1977
Create Date: 2026-07-17 10:12:00.000000

Agent 初审执行审计表（设计 §2.3 / §3.2）：
- task_id = 幂等键 cps-rectify-{issue_id}-v{version_no}，唯一索引兜底重复 C-01；
- UNIQUE(issue_id, version_no) 与 Java 侧 cps_initial_review_task 对齐；
- status 六态对齐 Java ENUM：RUNNING/FAILED/TIMEOUT_OPEN/COMPLETED/TAKEN_OVER/LATE_RESULT
  （Python 执行侧只写 RUNNING/FAILED/COMPLETED，见 app/models/agent_initial_review.py）；
- deadline_at = started_at + 480s（8 分钟 wall-clock，设计 §3.1）；
- text_checks / model_checks / input_snapshot 用 PG JSONB；
- Index(status, deadline_at) 支撑超时惰性扫描。

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b41c7f2ae903'
down_revision: str | Sequence[str] | None = '9d35bb4a1977'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agent_initial_review_exec',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=128), nullable=False),
        sa.Column('issue_id', sa.String(length=64), nullable=False),
        sa.Column('version_no', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('overall', sa.String(length=16), nullable=True),
        sa.Column('model_version', sa.String(length=128), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=True),
        sa.Column('input_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('text_checks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('model_checks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('callback_status', sa.String(length=24), nullable=False),
        sa.Column('callback_attempts', sa.Integer(), nullable=False),
        sa.Column('callback_last_error', sa.Text(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issue_id', 'version_no', name='uq_agent_initial_review_issue_version'),
    )
    op.create_index(
        op.f('ix_agent_initial_review_exec_issue_id'),
        'agent_initial_review_exec', ['issue_id'], unique=False,
    )
    # task_id：ORM 声明 unique=True + index=True → 唯一索引（幂等键兜底）
    op.create_index(
        op.f('ix_agent_initial_review_exec_task_id'),
        'agent_initial_review_exec', ['task_id'], unique=True,
    )
    op.create_index(
        'ix_agent_initial_review_status_deadline',
        'agent_initial_review_exec', ['status', 'deadline_at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_agent_initial_review_status_deadline', table_name='agent_initial_review_exec'
    )
    op.drop_index(
        op.f('ix_agent_initial_review_exec_task_id'), table_name='agent_initial_review_exec'
    )
    op.drop_index(
        op.f('ix_agent_initial_review_exec_issue_id'), table_name='agent_initial_review_exec'
    )
    op.drop_table('agent_initial_review_exec')
