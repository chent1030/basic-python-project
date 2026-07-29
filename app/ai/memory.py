"""持久记忆存储(MemoryStore)—— agent_memories 表(独立向量库)。

跨会话的事实/偏好,带向量用于语义召回。存在独立向量库数据源
(settings.agents.persistent_memory.datasource,与业务库隔离)。

embedding 复用 llm.providers 的某个 provider(OpenAIEmbeddings,走 OpenAI 兼容端点)。
persistent_memory.enabled=false 时,MemoryStore 方法退化为 no-op(不报错),
中间件据此决定是否注入记忆。

召回:把 query 向量化,用 pgvector 的余弦距离(<=>)找 top_k 最近邻。
写入:把 content 向量化后插入。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import PersistentMemoryConfig, settings
from app.core.datasource import datasources
from app.core.logging_config import get_logger

log = get_logger("app.ai.memory")


# 缓存:provider -> OpenAIEmbeddings 实例(惰性构建,避免启动时强依赖)
_embedding_cache: dict[str, Any] = {}


def _pm_cfg() -> PersistentMemoryConfig:
    return settings.agents.persistent_memory


def _enabled() -> bool:
    return _pm_cfg().enabled and bool(_pm_cfg().datasource)


def _session_factory():
    """持久记忆所用数据源的 session factory(独立向量库)。"""
    ds = _pm_cfg().datasource
    return datasources.get_session_factory(ds)


def _get_embedder():
    """惰性构建 embedding 客户端(复用 llm.providers 配置)。"""
    cfg = _pm_cfg()
    provider = cfg.embedding_provider or settings.llm.default_provider
    if provider in _embedding_cache:
        return _embedding_cache[provider]

    from langchain_openai import OpenAIEmbeddings

    prov = settings.llm.providers[provider]
    embedder = OpenAIEmbeddings(
        model=cfg.embedding_model or "text-embedding-3-small",
        base_url=prov.base_url,
        api_key=prov.api_key,
    )
    _embedding_cache[provider] = embedder
    return embedder


class MemoryStore:
    """持久记忆读写(独立向量库)。未启用时所有方法为 no-op。"""

    async def recall(
        self, agent_name: str, query: str, *, user_id: str | None = None, top_k: int | None = None
    ) -> list[str]:
        """向量召回与 query 最相关的若干条记忆(content 文本)。未启用返回 []。"""
        if not _enabled():
            return []
        try:
            from pgvector.sqlalchemy import Vector  # noqa: F401  确保类型注册

            from app.models.agent_memory import AgentMemory

            embedder = _get_embedder()
            qvec = await embedder.aembed_query(query)
            cfg = _pm_cfg()
            k = top_k or cfg.top_k

            async with _session_factory()() as session:
                dist = AgentMemory.embedding.cosine_distance(qvec).label("dist")
                stmt = (
                    select(AgentMemory.content, dist)
                    .where(AgentMemory.agent_name == agent_name)
                )
                if user_id:
                    stmt = stmt.where(
                        (AgentMemory.user_id == user_id) | (AgentMemory.user_id.is_(None))
                    )
                stmt = stmt.order_by("dist").limit(k)
                result = await session.execute(stmt)
                return [row[0] for row in result.all()]
        except Exception:
            log.exception("持久记忆召回失败 agent=%s query=%s", agent_name, query[:50])
            return []

    async def remember(
        self,
        agent_name: str,
        content: str,
        *,
        user_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """写入一条持久记忆(向量化后存)。未启用或失败只记日志。"""
        if not _enabled():
            return
        try:
            from app.models.agent_memory import AgentMemory

            embedder = _get_embedder()
            vec = await embedder.aembed_query(content)
            async with _session_factory()() as session:
                session.add(
                    AgentMemory(
                        agent_name=agent_name,
                        user_id=user_id,
                        content=content,
                        embedding=vec,
                        metadata_=metadata or {},
                    )
                )
                await session.commit()
        except Exception:
            log.exception("持久记忆写入失败 agent=%s", agent_name)


# 单例
memory_store = MemoryStore()


__all__ = ["MemoryStore", "memory_store"]
