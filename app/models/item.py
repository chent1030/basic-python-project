"""Example ORM model.

NOTE on multi-datasource: a model class is bound to a *database schema*, not
to a connection. The same `Item` model can be read/written from PostgreSQL
primary, the read-replica, or even MySQL — depending on which AsyncSession
you use. To keep schemas separate across engines, declare per-DS models
under different `__tablename__` values or different `Base` subclasses.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
