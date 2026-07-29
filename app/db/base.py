"""Shared SQLAlchemy declarative base for SQL models."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Subclass this for ORM models. Used by any SQL datasource."""
