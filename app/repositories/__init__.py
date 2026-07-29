"""Repository layer — all SQL / DB access lives here.

层次约定:
- endpoints / services 不直接写 SQL,只调 repository 方法
- 每个 repository 类对应一个领域模型(或一组相关表),持有 AsyncSession
- 一个 repository 实例 = 一次请求(因为 AsyncSession 是 per-request 的)

示例:
    # 在 endpoint 里:
    async def my_route(db: DbPostgresPrimary, id_: int):
        repo = ItemRepository(db)
        item = await repo.get(id_)
        ...

两种风格并存(按需选用):
- ORM 风格:用 SQLAlchemy ORM(Item / select(Item))— 默认推荐,类型安全
- 原生 SQL:用 text() + session.execute()— 复杂查询/性能优化时用
"""
from app.repositories.item_repository import ItemRepository

__all__ = ["ItemRepository"]
