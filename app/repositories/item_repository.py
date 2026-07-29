"""ItemRepository — example repository showing both ORM and raw SQL styles.

Usage from an endpoint:
    async def list_items(db: DbPostgresPrimary):
        repo = ItemRepository(db)
        return await repo.list(limit=50)

    # 领域特有查询(写在这里,而不是 endpoint):
    async def search(db: DbPostgresPrimary, keyword: str):
        repo = ItemRepository(db)
        return await repo.search_by_name(keyword)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import Item
from app.repositories.base import BaseRepository


class ItemRepository(BaseRepository[Item]):
    """所有跟 items 表相关的 SQL 都集中在这里。"""

    model = Item

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------- ORM 风格(推荐)
    async def search_by_name(self, keyword: str, *, limit: int = 50) -> list[Item]:
        """按名称模糊查询(ORM 风格)。"""
        stmt = (
            select(Item)
            .where(Item.name.ilike(f"%{keyword}%"))
            .order_by(Item.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        """总数(ORM 风格)。"""
        from sqlalchemy import func

        result = await self.session.execute(select(func.count()).select_from(Item))
        return int(result.scalar_one())

    # ------------------------------------------------- 原生 SQL 风格
    # 适合:复杂 JOIN、聚合、数据库特定语法、手写优化的 SQL。
    async def raw_search(self, keyword: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """原生 SQL 查询,返回 dict 列表(不绑 ORM 模型)。

        注意:
        - 永远用 :param 占位符传参,不要用 f-string 拼(防 SQL 注入)
        - LIMIT :limit 在 PostgreSQL/MySQL 都支持
        """
        sql = text(
            """
            SELECT id, name, description, created_at
            FROM items
            WHERE name ILIKE :pattern
            ORDER BY id DESC
            LIMIT :limit
            """
        )
        # MySQL 不支持 ILIKE,需要改成 LOWER(name) LIKE LOWER(:pattern)
        # 这里用 PostgreSQL 语法;多数据源时建议 ORM 风格更可移植
        result = await self.session.execute(
            sql, {"pattern": f"%{keyword}%", "limit": limit}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]

    async def raw_stats(self) -> dict[str, Any]:
        """聚合查询示例:统计每天条目数。原生 SQL 更直观。"""
        sql = text(
            """
            SELECT DATE(created_at) AS day, COUNT(*) AS cnt
            FROM items
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT 30
            """
        )
        result = await self.session.execute(sql)
        rows = result.mappings().all()
        return {"daily_counts": [dict(r) for r in rows]}
