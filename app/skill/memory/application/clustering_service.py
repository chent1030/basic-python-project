"""记忆 clustering 服务：把 ADJUDICATION 聚类成 memory_skill_pattern。

本波实现「轻量聚合」（按 category_l1 + area 简单 group），仅契约贯通；真实聚类
（embedding 相似度聚类 / LLM 总结 pattern_summary）由后续波次实现。
"""
from __future__ import annotations

import logging

from app.skill.memory.domain.models import MemorySkillPattern
from app.skill.memory.infrastructure.repository import MemoryRepository

logger = logging.getLogger(__name__)


class MemoryClusteringService:
    """语义聚类服务。"""

    def __init__(self, repository: MemoryRepository) -> None:
        self._repo = repository

    async def cluster_pending(self, limit: int = 200) -> list[MemorySkillPattern]:
        """聚合未聚类的 ADJUDICATION → upsert memory_skill_pattern。"""
        return await self._repo.cluster_pending(limit=limit)

    async def list_recent(self, top_k: int = 50) -> list[MemorySkillPattern]:
        """回看最近 top_k 个 pattern（按 updated_at desc）。"""
        return await self._repo.list_patterns(top_k=top_k)


__all__ = ["MemoryClusteringService"]