"""Tests for datasource pool / timeout configuration.

Verifies that the Hikari-style params from config are correctly translated
into SQLAlchemy engine + driver connect_args, and into Redis socket options.
"""
from __future__ import annotations

import pytest

from app.core.config import DatasourceConfig
from app.core.datasource import DatasourceManager, _build_connect_args


# ----------------------------------------------------------- connect_args
def test_connect_args_postgresql_with_statement_timeout():
    cfg = DatasourceConfig(
        type="postgresql",
        dsn="postgresql+asyncpg://u:p@h:5432/db",
        connect_timeout=8,
        statement_timeout=25000,
    )
    args = _build_connect_args(cfg)
    assert args["timeout"] == 8
    assert args["server_settings"]["statement_timeout"] == "25000"


def test_connect_args_postgresql_without_statement_timeout():
    cfg = DatasourceConfig(
        type="postgresql",
        dsn="postgresql+asyncpg://u:p@h:5432/db",
        connect_timeout=10,
        statement_timeout=None,
    )
    args = _build_connect_args(cfg)
    assert args["timeout"] == 10
    # server_settings should not be present when statement_timeout is None
    assert "server_settings" not in args


def test_connect_args_mysql():
    cfg = DatasourceConfig(
        type="mysql",
        dsn="mysql+aiomysql://u:p@h:3306/db",
        connect_timeout=12,
    )
    args = _build_connect_args(cfg)
    assert args == {"connect_timeout": 12}


def test_connect_args_redis_returns_empty():
    cfg = DatasourceConfig(type="redis", dsn="redis://h:6379/0")
    # connect_args only meaningful for SQL drivers
    assert _build_connect_args(cfg) == {}


# ------------------------------------------- engine creation (mocked)
@pytest.mark.asyncio
async def test_sql_engine_receives_all_pool_params(monkeypatch):
    """Verify create_async_engine is called with the full Hikari-style param set."""
    captured: dict = {}

    def fake_create_async_engine(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        # Return a dummy — we won't actually use it
        return object()

    monkeypatch.setattr(
        "app.core.datasource.create_async_engine", fake_create_async_engine
    )
    monkeypatch.setattr(
        "app.core.datasource.async_sessionmaker",
        lambda engine, **kw: object(),  # noqa: ARG005
    )

    cfg = DatasourceConfig(
        type="postgresql",
        dsn="postgresql+asyncpg://u:p@h:5432/db",
        pool_size=7,
        max_overflow=13,
        pool_timeout=20.0,
        pool_recycle=900,
        pool_pre_ping=False,
        connect_timeout=5,
        statement_timeout=10000,
    )
    monkeypatch.setattr(
        "app.core.datasource.settings",
        type("S", (), {"datasources": {"pg": cfg}})(),
    )

    mgr = DatasourceManager()
    await mgr.startup()

    kw = captured["kwargs"]
    # Pool sizing
    assert kw["pool_size"] == 7
    assert kw["max_overflow"] == 13
    # Timeouts
    assert kw["pool_timeout"] == 20.0
    assert kw["pool_recycle"] == 900
    assert kw["pool_pre_ping"] is False
    assert kw["pool_use_lifo"] is True  # keep hot connections resident
    # pool_keepalive is intentionally NOT passed (async engine doesn't support it)
    assert kw["pool_use_lifo"] is True
    # Driver connect_args
    assert kw["connect_args"]["timeout"] == 5
    assert kw["connect_args"]["server_settings"]["statement_timeout"] == "10000"


@pytest.mark.asyncio
async def test_redis_client_receives_socket_params(monkeypatch):
    """Verify from_url gets socket_timeout / connect_timeout / health_check."""
    captured: dict = {}

    class _FakeRedis:
        async def aclose(self):
            pass

    def fake_from_url(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return _FakeRedis()

    monkeypatch.setattr("app.core.datasource.from_url", fake_from_url)

    cfg = DatasourceConfig(
        type="redis",
        dsn="redis://h:6379/0",
        max_connections=42,
        socket_timeout=3.5,
        socket_connect_timeout=4.5,
        health_check_interval=60,
    )
    monkeypatch.setattr(
        "app.core.datasource.settings",
        type("S", (), {"datasources": {"cache": cfg}})(),
    )

    mgr = DatasourceManager()
    await mgr.startup()

    kw = captured["kwargs"]
    assert kw["max_connections"] == 42
    assert kw["socket_timeout"] == 3.5
    assert kw["socket_connect_timeout"] == 4.5
    assert kw["health_check_interval"] == 60

    # Cleanup shouldn't raise even though we used a fake client
    await mgr.shutdown()


# -------------------------------------------------- config defaults sane
def test_default_pool_params_match_hikari_conventions():
    """Defaults should be sensible (mirroring Hikari defaults where reasonable)."""
    pg = DatasourceConfig(type="postgresql", dsn="x")
    # 30s connection timeout is Hikari's default connectionTimeout
    assert pg.pool_timeout == 30.0
    assert pg.pool_pre_ping is True
    assert pg.connect_timeout == 10
    # keepalive not part of async engine; recycle + pre_ping cover that role
    assert pg.statement_timeout is None

    r = DatasourceConfig(type="redis", dsn="x")
    assert r.socket_timeout == 5.0
    assert r.health_check_interval == 30
