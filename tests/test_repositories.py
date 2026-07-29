"""Tests for the repository layer — verifies both ORM and raw SQL styles.

Uses in-memory SQLite so no external DB is needed. The same repository code
works on PostgreSQL / MySQL / SQLite (ORM style); raw SQL portability is
noted in item_repository.py.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories import ItemRepository


@pytest.fixture(scope="module")
def engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:")


@pytest.fixture(scope="module")
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module")
def _create_tables(engine):
    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())


@pytest.fixture
async def session(session_factory, _create_tables) -> AsyncSession:  # noqa: ARG001
    async with session_factory() as s:
        yield s


# ---------------------------------------------------------------- ORM style
@pytest.mark.asyncio
async def test_repo_create_and_get(session: AsyncSession):
    repo = ItemRepository(session)
    item = await repo.create(name="hello", description="world")
    await repo.commit()

    fetched = await repo.get(item.id)
    assert fetched is not None
    assert fetched.name == "hello"
    assert fetched.description == "world"


@pytest.mark.asyncio
async def test_repo_list_with_pagination(session: AsyncSession):
    repo = ItemRepository(session)
    for i in range(5):
        await repo.create(name=f"item-{i}")
    await repo.commit()

    all_items = await repo.list(limit=100)
    assert len(all_items) >= 5

    page = await repo.list(limit=2, offset=0)
    assert len(page) == 2


@pytest.mark.asyncio
async def test_repo_search_by_name(session: AsyncSession):
    repo = ItemRepository(session)
    await repo.create(name="apple pie")
    await repo.create(name="banana")
    await repo.create(name="pineapple")
    await repo.commit()

    found = await repo.search_by_name("apple")
    names = {i.name for i in found}
    # ILIKE on sqlite falls back to LIKE (case-insensitive for ASCII).
    assert "apple pie" in names
    assert "pineapple" in names
    assert "banana" not in names


@pytest.mark.asyncio
async def test_repo_count(session: AsyncSession):
    repo = ItemRepository(session)
    before = await repo.count()
    await repo.create(name="counter")
    await repo.commit()
    after = await repo.count()
    assert after == before + 1


# ---------------------------------------------------------------- Raw SQL
@pytest.mark.asyncio
async def test_repo_fetch_all_raw_sql(session: AsyncSession):
    repo = ItemRepository(session)
    await repo.create(name="raw1")
    await repo.create(name="raw2")
    await repo.commit()

    rows = await repo.fetch_all(
        "SELECT id, name FROM items WHERE name LIKE :p ORDER BY name",
        {"p": "raw%"},
    )
    assert len(rows) == 2
    names = {r.name for r in rows}  # Row is attribute-accessible
    assert names == {"raw1", "raw2"}


@pytest.mark.asyncio
async def test_repo_fetch_one_raw_sql(session: AsyncSession):
    repo = ItemRepository(session)
    await repo.create(name="unique-find-me")
    await repo.commit()

    row = await repo.fetch_one(
        "SELECT name FROM items WHERE name = :n",
        {"n": "unique-find-me"},
    )
    assert row is not None
    assert row.name == "unique-find-me"


@pytest.mark.asyncio
async def test_repo_execute_raw_insert_update(session: AsyncSession):
    repo = ItemRepository(session)
    # Raw INSERT
    await repo.execute(
        "INSERT INTO items (name, description) VALUES (:n, :d)",
        {"n": "raw-inserted", "d": "via sql"},
    )
    await repo.commit()

    row = await repo.fetch_one("SELECT name FROM items WHERE name = :n", {"n": "raw-inserted"})
    assert row is not None
    assert row.name == "raw-inserted"


@pytest.mark.asyncio
async def test_repo_delete(session: AsyncSession):
    repo = ItemRepository(session)
    item = await repo.create(name="to-delete")
    await repo.commit()

    fetched = await repo.get(item.id)
    assert fetched is not None
    await repo.delete(fetched)
    await repo.commit()

    assert await repo.get(item.id) is None
