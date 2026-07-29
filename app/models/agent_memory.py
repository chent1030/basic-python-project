"""持久记忆(persistent memory)ORM 模型 —— 独立向量库,跨会话事实/偏好。

存在独立的向量库数据源(通常是独立 PG + pgvector 扩展),与业务库隔离。
通过向量召回相关记忆注入 prompt,跨会话记住用户偏好/事实。

注意:本表依赖 pgvector 扩展。embedding 维度取决于 embedding_model,
建表前需在目标库执行 `CREATE EXTENSION IF NOT EXISTS vector;`。
persistent_memory 未启用时本表不会被使用。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Vector 维度默认 1536(OpenAI text-embedding-3-small 等),可按 embedding_model 调整。
# 这里用一个模块级常量,供迁移和 MemoryStore 参考;实际维度以 embedding_model 为准。
EMBEDDING_DIM = 1536


def _vector_type(dim: int = EMBEDDING_DIM):
    """惰性构造 pgvector 的 Vector 列类型。

    放在函数里惰性 import,避免 pgvector 未安装时整个模型模块 import 失败
    (例如只在业务库跑迁移时)。pgvector 已在依赖中,正常都会装上。
    """
    from pgvector.sqlalchemy import Vector

    return Vector(dim)


class AgentMemory(Base):
    """一条持久记忆(跨会话事实/偏好),带向量用于召回。

    metadata_ 字段名加下划线避免与 SQLAlchemy 保留属性冲突(属性名 metadata_)。
    """

    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    # embedding 用 mapped_column + 类型在类创建时求值;惰性构造放 __init_subclass__ 不便,
    # 改用延迟表达式:列定义为 Vector(EMBEDDING_DIM)。
    embedding = mapped_column(_vector_type(EMBEDDING_DIM), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True
    )  # JSON(PG 可后续改为 JSONB,SQLite/MySQL 用 JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_agent_memories_agent_user", "agent_name", "user_id"),
    )
