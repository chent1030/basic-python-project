"""Items endpoints — demo: switching datasources via annotations.

每个 endpoint 通过注入的 AsyncSession 构造一个 repository,所有 SQL 都
写在 repository 层(app/repositories/),endpoint 不写任何查询代码。

三种数据源切换风格仍并排展示:
1. Annotated type alias  -> `db: DbPostgresPrimary`
2. Decorator             -> `@use_datasource("mysql_business")`
3. Plain Depends()       -> `Depends(get_db("postgres_readonly"))`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datasource import (
    DbPostgresPrimary,
    DbPostgresReadonly,
    get_db,
    use_datasource,
)
from app.db.base import Base
from app.repositories import ItemRepository
from app.schemas.item import ItemCreate, ItemOut

router = APIRouter(prefix="/items", tags=["items"])


@router.post("/pg/init", response_model=dict)
async def init_postgres_primary(db: DbPostgresPrimary) -> dict[str, str]:
    """Create tables on the PostgreSQL primary datasource."""
    await db.run_sync(Base.metadata.create_all)
    await db.commit()
    return {"status": "created", "datasource": "postgres_primary"}


# ---------------------------------------------------------------- 1. Annotated
@router.post("/pg", response_model=ItemOut)
async def create_on_postgres(payload: ItemCreate, db: DbPostgresPrimary) -> ItemOut:
    """写 PostgreSQL 主库 —— Annotated 别名注入 session,SQL 在 repository。"""
    repo = ItemRepository(db)
    item = await repo.create(name=payload.name, description=payload.description)
    await repo.commit()
    return ItemOut.model_validate(item)


@router.get("/pg", response_model=list[ItemOut])
async def list_from_postgres_readonly(db: DbPostgresReadonly) -> list[ItemOut]:
    """读 PostgreSQL 只读副本。"""
    repo = ItemRepository(db)
    items = await repo.list(limit=100)
    return [ItemOut.model_validate(i) for i in items]


@router.get("/pg/search", response_model=list[ItemOut])
async def search_postgres(db: DbPostgresReadonly, q: str) -> list[ItemOut]:
    """演示 repository 里 ORM 风格的领域查询(ILIKE 模糊搜索)。"""
    repo = ItemRepository(db)
    items = await repo.search_by_name(q)
    return [ItemOut.model_validate(i) for i in items]


# ---------------------------------------------------------------- 2. Decorator
@router.post("/mysql", response_model=ItemOut)
@use_datasource("mysql_business")
async def create_on_mysql(payload: ItemCreate, db: AsyncSession) -> ItemOut:
    """写 MySQL —— @use_datasource 装饰器注入,SQL 仍在 repository。"""
    repo = ItemRepository(db)
    item = await repo.create(name=payload.name, description=payload.description)
    await repo.commit()
    return ItemOut.model_validate(item)


@router.get("/mysql", response_model=list[ItemOut])
@use_datasource("mysql_business")
async def list_from_mysql(db: AsyncSession) -> list[ItemOut]:
    repo = ItemRepository(db)
    items = await repo.list(limit=100)
    return [ItemOut.model_validate(i) for i in items]


# --------------------------------------------- 3. Plain Depends() (reference)
@router.get("/pg/all")
async def list_all_via_depends(
    db: AsyncSession = Depends(get_db("postgres_readonly")),
):
    """原生 Depends() 写法,SQL 仍在 repository。"""
    repo = ItemRepository(db)
    items = await repo.list()
    return [ItemOut.model_validate(i) for i in items]
