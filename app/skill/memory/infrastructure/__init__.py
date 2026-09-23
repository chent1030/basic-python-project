"""记忆基础设施：仓库、向量适配、Embedding 客户端、Java 端对接。"""
from app.skill.memory.infrastructure.bootstrap import ensure_schema
from app.skill.memory.infrastructure.embedding_client import (
    EmbeddingClient,
    HashPlaceholderEmbedding,
    make_default_embedding_client,
)
from app.skill.memory.infrastructure.java_client import (
    CPS_BACKEND_URL_ENV,
    CpsMemoryClient,
)
from app.skill.memory.infrastructure.pgvector_adapter import PgVectorAdapter
from app.skill.memory.infrastructure.repository import MemoryRepository

__all__ = [
    "CPS_BACKEND_URL_ENV",
    "CpsMemoryClient",
    "EmbeddingClient",
    "HashPlaceholderEmbedding",
    "MemoryRepository",
    "PgVectorAdapter",
    "ensure_schema",
    "make_default_embedding_client",
]