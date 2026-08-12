"""会话历史存储(业务库)。DB 不可用时静默降级。"""
from __future__ import annotations

from app.core.config import settings
from app.core.datasource import datasources


def _factory():
    return datasources.get_session_factory(
        settings.harness.session_datasource
    )

class SessionStore:
    async def load_history(self, agent_name, session_id, *, limit=50):
        try:
            from sqlalchemy import select

            from app.models.agent_session import AgentSession
            async with _factory()() as session:
                stmt = (
                    select(AgentSession)
                    .where(
                        AgentSession.agent_name == agent_name,
                        AgentSession.session_id == session_id,
                    )
                    .order_by(AgentSession.id.desc())
                    .limit(limit)
                )
                rows = list((await session.execute(stmt)).scalars().all())
            rows.reverse()
            return [{"role": r.role, "content": r.content} for r in rows]
        except Exception:
            return []
    async def append_message(self, agent_name, session_id, role, content):
        try:
            from app.models.agent_session import AgentSession
            async with _factory()() as session:
                session.add(AgentSession(
                    agent_name=agent_name,
                    session_id=session_id,
                    role=role,
                    content=content,
                ))
                await session.commit()
        except Exception:
            pass

session_store = SessionStore()
__all__ = ["SessionStore", "session_store"]
