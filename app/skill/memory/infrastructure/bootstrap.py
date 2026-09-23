"""记忆基础设施启动钩子：测试/启动期保证表存在。

生产用 ``alembic upgrade head``（由迁移 V20260927__memory_schema.py 建表）；
本函数用于：
- 测试 conftest（不跑 alembic，建表直接走 Base.metadata.create_all）；
- dev 环境跳过 alembic 时快速引导。

设计取舍：不动 ``Base.metadata** 之外的状态——只 ensure 表/索引存在；数据回填由
``MemoryIngestionService.backfill_since`` 完成（独立入口）。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def ensure_schema(engine: AsyncEngine) -> None:
    """检查 memory_entry / memory_skill_pattern / memory_metric_snapshot 是否就绪。

    存在则跳过；不存在则 ``Base.metadata.create_all`` 兜底建表（仅 dev/test 使用）。
    """
    import app.skill.memory.infrastructure.repository as _repo  # noqa: F401  # 注册 ORM
    from app.db.base import Base

    async with engine.begin() as conn:
        def _sync_inspect(sync_conn: object) -> set[str]:
            insp = inspect(sync_conn)
            return set(insp.get_table_names())

        existing = await conn.run_sync(_sync_inspect)
        required = {
            "memory_entry",
            "memory_skill_pattern",
            "memory_metric_snapshot",
        }
        if required.issubset(existing):
            logger.debug("memory schema already present (tables=%s)", required)
            return
        # 缺失——create_all 补建（dev fallback；测试避免拉 alembic）
        logger.info("memory schema missing tables=%s, creating...", required - existing)
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["ensure_schema"]