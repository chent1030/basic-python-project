"""Smoke test: verify app boots and core endpoints behave correctly.

Strategy: bypass the real lifespan (which would try to connect to real
MySQL/PostgreSQL/Redis) by NOT using `with TestClient(app)`, and instead
patch the datasource manager's accessors to return in-memory equivalents.
FastAPI dependency_overrides is used to swap the SQL/Redis providers.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.endpoints import items as items_module
from app.core.datasource import (
    _get_redis_provider,
    _get_sql_provider,
    datasources,
)
from app.db.base import Base
from app.main import app

# In-memory SQLite used to back every SQL datasource during tests.
_test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_test_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


class _FakeRedis:
    async def ping(self):
        return True

    async def aclose(self):
        pass


async def _override_sql() -> AsyncSession:
    async with _test_factory() as session:
        yield session


async def _override_redis() -> _FakeRedis:
    return _FakeRedis()


def _create_tables() -> None:
    async def _init():
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())


@pytest.fixture(scope="module")
def client():
    # 在 settings.datasources 里注册假配置(让 provider 的类型解析能通过)。
    # 注意:DSN 留空 + host 留空,is_configured() 返回 False,
    # 这样 lifespan 的 startup 会跳过它们(不会真去建 engine),
    # 而我们手动注册 _session_factories / _redis 给 provider 用。
    from app.core.config import DatasourceConfig, settings

    for name, t in [
        ("postgres_primary", "postgresql"),
        ("postgres_readonly", "postgresql"),
        ("mysql_business", "mysql"),
        ("redis_cache", "redis"),
    ]:
        settings.datasources[name] = DatasourceConfig(type=t)  # 空, is_configured=False

    # Register the fake datasources so introspection endpoints report them.
    datasources._types.update(
        postgres_primary="postgresql",
        postgres_readonly="postgresql",
        mysql_business="mysql",
        redis_cache="redis",
    )
    # 给 manager 注册 session factory / redis client,这样 _make_typed_provider
    # 调用 datasources.get_session_factory(name) / get_redis(name) 时能拿到。
    for name in ("postgres_primary", "postgres_readonly", "mysql_business"):
        datasources._session_factories[name] = _test_factory
    datasources._redis["redis_cache"] = _FakeRedis()

    _create_tables()

    # 同时用 dependency_overrides 覆盖 _get_sql_provider / _get_redis_provider,
    # 这样用 Annotated 别名(DbPostgresPrimary 等)的 endpoint 也能走 mock。
    for name in ("postgres_primary", "postgres_readonly", "mysql_business"):
        app.dependency_overrides[_get_sql_provider(name)] = _override_sql
    app.dependency_overrides[_get_redis_provider("redis_cache")] = _override_redis

    # TestClient 触发 lifespan,但 startup 会跳过未配置(is_configured=False)的数据源。
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    datasources._session_factories.clear()
    datasources._redis.clear()


# --------------------------------- tests ------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_public_endpoint(client):
    r = client.get("/api/v1/auth/public")
    assert r.status_code == 200
    assert r.json()["message"]


def test_protected_endpoint_requires_token(client):
    """@require_auth enforces 401 even with global auth off."""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_protected_endpoint_with_token(client):
    from app.core.security import create_access_token

    token = create_access_token({"username": "alice"})
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "alice"


def test_decorator_style_endpoint_works(client):
    """The @use_datasource decorator must successfully inject the session."""
    r = client.post("/api/v1/items/mysql", json={"name": "hello"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "hello"

    r = client.get("/api/v1/items/mysql")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_annotated_alias_endpoint_works(client):
    r = client.post("/api/v1/items/pg", json={"name": "from-pg"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "from-pg"


def test_redis_ping(client):
    r = client.get("/api/v1/datasources/redis/ping")
    assert r.status_code == 200
    assert r.json()["pong"] is True


def test_decorators_preserve_signatures():
    """Ensure @use_datasource injected Depends() as the default for `db`."""
    sig = inspect.signature(items_module.create_on_mysql)
    db_default = sig.parameters["db"].default
    assert db_default is not inspect.Parameter.empty
