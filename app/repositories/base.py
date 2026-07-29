"""Base repository with common CRUD helpers.

Subclass it to get get/list/create/update/delete for free, then add
domain-specific methods. Two query styles are provided side-by-side:

- ORM style (recommended): uses SQLAlchemy ORM (`select(Model)`)
- Raw SQL style: uses `text()` for complex queries or hand-tuned SQL

Both styles work on the same AsyncSession, regardless of whether the
datasource is PostgreSQL or MySQL.
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async repository bound to one model + one session.

    The session comes from a datasource (via dependency injection), so the
    same repository code works against PostgreSQL primary, read-replica,
    or MySQL — depending on which session you pass in.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- ORM CRUD
    async def get(self, id_: int) -> ModelT | None:
        """Fetch one row by primary key. Returns None if not found."""
        return await self.session.get(self.model, id_)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """List rows with pagination (newest first by id)."""
        stmt = (
            select(self.model)
            .order_by(self.model.id.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **fields: Any) -> ModelT:
        """Insert one row. Caller decides whether to commit."""
        obj = self.model(**fields)  # type: ignore[call-arg]
        self.session.add(obj)
        await self.session.flush()        # populate obj.id without committing
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        """Delete a tracked instance. Caller commits."""
        await self.session.delete(obj)
        await self.session.flush()

    async def commit(self) -> None:
        await self.session.commit()

    # ------------------------------------------------------- Raw SQL helpers
    async def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Run raw SQL, return all rows. Use for complex queries.

        Example:
            rows = await repo.fetch_all(
                "SELECT id, name FROM items WHERE name LIKE :pattern",
                {"pattern": "test%"},
            )
        """
        result = await self.session.execute(text(sql), params or {})
        return list(result.all())

    async def fetch_one(self, sql: str, params: dict[str, Any] | None = None) -> Any | None:
        """Run raw SQL, return the first row or None."""
        result = await self.session.execute(text(sql), params or {})
        return result.first()

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Run raw SQL that doesn't return rows (INSERT/UPDATE/DELETE/DDL).

        Returns the result; call `result.rowcount` for affected rows.
        """
        result = await self.session.execute(text(sql), params or {})
        return result
