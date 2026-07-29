"""会话记忆(session memory)ORM 模型。

存同一 session_id 下多轮往复的消息历史(用户/助手/系统)。
表名可被 config.yaml 的 agents.session_table 覆盖。

与持久记忆(agent_memories,向量库)的区别:
- agent_sessions 存原始对话消息,按 session_id 检索,走业务库
- agent_memories 存跨会话的事实/偏好,向量召回,走独立向量库
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentSession(Base):
    """单条会话消息。一个 session_id 下多条消息组成一段对话历史。"""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64))
    agent_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_agent_sessions_session", "session_id", "agent_name"),
    )
