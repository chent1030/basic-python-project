"""memory schema — I-line 长期记忆三层骨架 (FR-09/10/11/12)

Revision ID: V20260927__memory_schema
Revises: c3a1b2d3e4f5
Create Date: 2026-09-27 00:00:00.000000

I-line 记忆体系（波次 9 设计 / 用户裁决：记忆体系是产品核心，agent 必须根据历史
审核情况自主进化，使用越久越贴合实际）。本迁移建三张表 + pgvector 索引：

- ``memory_entry`` 主表（事件层 + 人工裁决层共用）：
  source_table / source_id 决定三层归属（ADJUDICATION/EVENT/ISSUE）；
  UNIQUE(source_table, source_id) 兜底 Java 端重复推送幂等；
  superseded_by 字段支持「同源新版本软取代」——不删行只标记，审计可追。

- ``memory_skill_pattern`` 语义层（聚类产物，本波只建表，clustering 任务后续波次实现）：
  skill_code 唯一，由 (category_l1_id, area, scenario) 派生；
  embedding 用于「历史同类如何处理」语义检索。

- ``memory_metric_snapshot`` 评估快照（FR-12 留表，本波不消费）：
  period_start/period_end + metric_key + JSONB 值；给治理看板留埋点。

为什么 pgvector + JSONB 双轨：
- embedding 列用 vector(1024)（BGE-large 默认维度），语义检索走 ivfflat cosine ops；
- payload JSONB 保留裁决/事件原始细节，便于回查与无 embedding 时的字段过滤兜底。
两路并存不是冗余：JSONB 是事实源（可审计），embedding 是检索索引（性能）。

注：本迁移不在业务数据库内运行——I 线数据存 ai-experience-postgres（库名 cps_memory，
与本仓的 postgres_primary 分库）。alembic 维护的是 schema 形态，运行期连接由
memory infra bootstrap 决定（见 app/skill/memory/infrastructure/bootstrap.py）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "V20260927__memory_schema"
down_revision: str | Sequence[str] | None = "c3a1b2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # pgvector 扩展——ivfflat 索引必需；多库共享同一扩展名（CREATE EXTENSION IF NOT EXISTS）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------------------------------------------------------- memory_entry
    op.create_table(
        "memory_entry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "source_table", sa.String(length=32), nullable=False,
            comment="ADJUDICATION / EVENT / ISSUE",
        ),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("issue_id", sa.BigInteger(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=True),
        sa.Column("category_l1_id", sa.Integer(), nullable=True),
        sa.Column("category_l2_id", sa.Integer(), nullable=True),
        sa.Column("factory", sa.String(length=64), nullable=True),
        sa.Column("area", sa.String(length=64), nullable=True),
        sa.Column("severity", sa.SmallInteger(), nullable=True),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        ),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE"),
        ),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_table", "source_id", name="uq_memory_entry_source",
        ),
    )
    op.create_index(
        "ix_memory_entry_issue_id", "memory_entry", ["issue_id"], unique=False,
    )
    op.create_index(
        "ix_memory_entry_source", "memory_entry", ["source_table", "source_id"], unique=False,
    )
    op.create_index(
        "ix_memory_entry_active", "memory_entry", ["is_active"], unique=False,
    )
    op.create_index(
        "ix_memory_entry_factory_area", "memory_entry", ["factory", "area"], unique=False,
    )
    op.create_index(
        "ix_memory_entry_cat_l1", "memory_entry", ["category_l1_id"], unique=False,
    )

    # ----------------------------------------------------------- memory_skill_pattern
    op.create_table(
        "memory_skill_pattern",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "skill_code", sa.String(length=64), nullable=False,
            comment="语义聚类 key，派生自 (category_l1_id, area, scenario)",
        ),
        sa.Column("pattern_summary", sa.Text(), nullable=True),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column(
            "example_count", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_code", name="uq_memory_skill_pattern_code"),
    )

    # ----------------------------------------------------------- memory_metric_snapshot
    op.create_table(
        "memory_metric_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column(
            "metric_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        ),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_metric_snapshot_period",
        "memory_metric_snapshot",
        ["period_start", "period_end", "metric_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_memory_metric_snapshot_period", table_name="memory_metric_snapshot"
    )
    op.drop_table("memory_metric_snapshot")
    op.drop_table("memory_skill_pattern")
    op.drop_index("ix_memory_entry_cat_l1", table_name="memory_entry")
    op.drop_index("ix_memory_entry_factory_area", table_name="memory_entry")
    op.drop_index("ix_memory_entry_active", table_name="memory_entry")
    op.drop_index("ix_memory_entry_source", table_name="memory_entry")
    op.drop_index("ix_memory_entry_issue_id", table_name="memory_entry")
    op.drop_table("memory_entry")
    # 扩展不主动 drop（可能被其它库使用）