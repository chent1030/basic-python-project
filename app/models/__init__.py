"""ORM 模型包。

重要:新增模型文件后,必须在这里导入,否则 Alembic autogenerate 检测不到,
Base.metadata 也不会包含该表。

类似 Spring 的 @Entity 扫描,但 Python 需要显式导入触发类定义。
"""
from app.models.agent_memory import AgentMemory
from app.models.agent_run import AgentRun
from app.models.agent_session import AgentSession
from app.models.item import Item

__all__ = ["Item", "AgentSession", "AgentRun", "AgentMemory"]
