"""Introspection endpoints for datasources & Redis demo."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.datasource import RedisCache, datasources

router = APIRouter(prefix="/datasources", tags=["datasources"])


@router.get("")
async def list_datasources() -> dict[str, object]:
    """List all configured datasources and their types."""
    return {
        name: {"type": datasources.kind(name)} for name in datasources.names()
    }


@router.get("/redis/ping")
async def redis_ping(r: RedisCache) -> dict[str, bool]:
    """Switch datasource to Redis via the Annotated alias and PING it."""
    pong = await r.ping()
    return {"pong": bool(pong)}
