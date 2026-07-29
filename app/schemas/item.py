"""Pydantic schemas for the Item example."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ItemOut(BaseModel):
    # from_attributes=True 允许从 ORM 模型实例直接构造(ItemOut.model_validate(orm_obj))
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    created_at: datetime
