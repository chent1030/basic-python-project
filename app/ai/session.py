"""会话记忆存储(SessionStore)—— agent_sessions 表。

持续对话(chat)模式用:按 session_id 存每轮消息,下次调用前 load 历史拼进 messages。
走业务数据源(settings.agents.session_datasource,默认 postgres_primary)。

注意:这里不自己 commit 管理事务,每个方法独立开 session 提交(中间件场景下
没有现成的请求级 session 可复用,且写入较简单)。
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.datasource import datasources
from app.core.logging_config import get_logger
from app.models.agent_session import AgentSession

log = get_logger("app.ai.session")


def _session_factory():
    """取会话数据源的 session factory。"""
    return datasources.get_session_factory(settings.agents.session_datasource)


def _available() -> bool:
    """会话数据源是否就绪。未就绪时所有读写静默降级(返回空/不写)。"""
    return settings.agents.session_datasource in datasources._session_factories


class SessionStore:
    """会话历史读写(走业务数据源)。DB 不可用时静默降级,不影响 agent 主流程。"""

    async def load_history(
        self, agent_name: str, session_id: str, *, limit: int = 50
    ) -> list[dict[str, str]]:
        """加载某会话的历史消息(role/content),按时间正序,最多 limit 条。

        DB 不可用时返回空列表(无历史可加载)。
        """
        if not _available():
            return []
        try:
            async with _session_factory()() as session:
                stmt = (
                    select(AgentSession)
                    .where(
                        AgentSession.agent_name == agent_name,
                        AgentSession.session_id == session_id,
                    )
                    .order_by(AgentSession.id.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
            # 取最新 limit 条后反转为正序
            rows.reverse()
            return [{"role": r.role, "content": r.content} for r in rows]
        except Exception:
            log.debug("加载会话历史失败(数据源可能不可用) agent=%s", agent_name, exc_info=True)
            return []

    async def append_message(
        self, agent_name: str, session_id: str, role: str, content: str
    ) -> None:
        """追加一条消息到会话历史。DB 不可用或写失败时静默跳过。"""
        if not _available():
            return
        try:
            async with _session_factory()() as session:
                session.add(
                    AgentSession(
                        agent_name=agent_name,
                        session_id=session_id,
                        role=role,
                        content=content,
                    )
                )
                await session.commit()
        except Exception:
            log.debug("写会话历史失败(数据源可能不可用) agent=%s", agent_name, exc_info=True)

    async def append_turn(
        self, agent_name: str, session_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        """一次追加一轮(user + assistant)。失败不影响主流程(静默跳过)。"""
        await self.append_message(agent_name, session_id, "user", user_msg)
        await self.append_message(agent_name, session_id, "assistant", assistant_msg)


# 单例
session_store = SessionStore()


__all__ = ["SessionStore", "session_store"]
