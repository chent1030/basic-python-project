"""运行记录(agent run)ORM 模型 —— 树状,可还原整棵调用树。

每次 agent 运行(trigger/chat,以及多拓扑下的每个成员/子 agent 调用)都写一条。
通过 parent_run_id + depth 把单次运行的调用树串起来:
- 顶层运行 parent_run_id=NULL, depth=0
- 成员/子 agent 调用 parent_run_id=<父 run_id>, depth=父+1

这样既能看单次执行的输入/输出/耗时/状态,又能还原多 agent 拓扑的完整调用结构。

status 三态:running(进行中)/ succeeded(成功)/ failed(失败)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRun(Base):
    """单次 agent 运行记录(树状节点)。表名可被 config 的 agents.runs_table 覆盖。"""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64))  # uuid,本条运行的唯一 id
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    agent_name: Mapped[str] = mapped_column(String(100))
    trigger_source: Mapped[str] = mapped_column(String(20))  # api | scheduler | internal
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input: Mapped[str] = mapped_column(Text)  # 输入消息(JSON 或纯文本)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_agent_runs_agent", "agent_name"),
        Index("ix_agent_runs_parent", "parent_run_id"),
        Index("ix_agent_runs_session", "session_id"),
    )
