"""Multi-datasource manager.

Holds async SQLAlchemy engines (PostgreSQL / MySQL) and async Redis clients.
All connections are established once at app startup (lifespan) and disposed
at shutdown — long-lived connections for the whole process.

Two ways to *switch datasource by annotation* on an endpoint:

1. **Annotated type alias** (recommended, idiomatic FastAPI):
       from app.core.datasource import DbPostgresPrimary, DbRedisCache
       @router.get("/items")
       async def list_items(db: DbPostgresPrimary): ...
       async def cache_get(r: DbRedisCache): ...

2. **Decorator** — injects the dependency as a kwarg:
       from app.core.datasource import use_datasource
       @router.get("/items")
       @use_datasource("postgres_primary")          # default alias="db"
       async def list_items(db: AsyncSession): ...

You can also use the plain dependency directly:
       db: Annotated[AsyncSession, Depends(get_db("postgres_primary"))]
"""
from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from functools import wraps
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import DatasourceConfig, settings

try:
    from redis.asyncio import Redis, from_url  # type: ignore
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore
    from_url = None  # type: ignore


def _build_connect_args(cfg: DatasourceConfig) -> dict[str, Any]:
    """Build driver-level `connect_args` for create_async_engine.

    Per-driver params:
    - PostgreSQL (asyncpg): connect_timeout + server_settings.statement_timeout
    - MySQL (aiomysql):     connect_timeout (语句超时用 SET SESSION 指令,稍复杂,
                            通常靠应用层或 pool_recycle 控制)
    """
    t = cfg.type
    if t == "postgresql":
        args: dict[str, Any] = {"timeout": cfg.connect_timeout}
        if cfg.statement_timeout is not None:
            # asyncpg: 语句级超时,单位毫秒,通过 server_settings 下发
            args["server_settings"] = {"statement_timeout": str(cfg.statement_timeout)}
        return args
    if t == "mysql":
        # aiomysql: connect_timeout 单位秒
        return {"connect_timeout": cfg.connect_timeout}
    return {}


class DatasourceManager:
    """Owns all async engines + redis clients keyed by datasource name."""

    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._redis: dict[str, Any] = {}
        self._types: dict[str, str] = {}

    # ---------- lifecycle (called from lifespan) -----------------------
    async def startup(self) -> None:
        for name, cfg in settings.datasources.items():
            # 未配置的数据源(host 和 dsn 都空)直接跳过,不创建连接
            if not cfg.is_configured():
                continue
            self._types[name] = cfg.type
            if cfg.type in ("postgresql", "mysql"):
                engine = create_async_engine(
                    cfg.dsn,
                    # ---- 连接池 ----
                    pool_size=cfg.pool_size,
                    max_overflow=cfg.max_overflow,
                    pool_timeout=cfg.pool_timeout,        # 池满等待秒数
                    pool_recycle=cfg.pool_recycle,        # 连接最大存活秒
                    pool_pre_ping=cfg.pool_pre_ping,     # 借出前探活
                    pool_use_lifo=True,                   # 后进先出,热连接常驻
                    # ---- 调试 ----
                    echo=cfg.echo,
                    # ---- 驱动层参数(connect_args)----
                    connect_args=_build_connect_args(cfg),
                )
                self._engines[name] = engine
                self._session_factories[name] = async_sessionmaker(
                    engine, class_=AsyncSession, expire_on_commit=False
                )
            elif cfg.type == "redis":
                if from_url is None:  # pragma: no cover
                    raise RuntimeError("redis package is required for redis datasources")
                self._redis[name] = from_url(
                    cfg.dsn,
                    max_connections=cfg.max_connections,
                    decode_responses=cfg.decode_responses,
                    socket_timeout=cfg.socket_timeout,
                    socket_connect_timeout=cfg.socket_connect_timeout,
                    health_check_interval=cfg.health_check_interval,
                )
            else:
                raise ValueError(f"Unknown datasource type: {cfg.type}")

    async def shutdown(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        for client in self._redis.values():
            await client.aclose()
        self._engines.clear()
        self._session_factories.clear()
        self._redis.clear()
        self._types.clear()

    # ---------- accessors ---------------------------------------------
    def names(self) -> list[str]:
        return list(self._types.keys())

    def kind(self, name: str) -> str:
        return self._types[name]

    def get_session_factory(self, name: str) -> async_sessionmaker[AsyncSession]:
        if name not in self._session_factories:
            raise KeyError(f"SQL datasource '{name}' not found")
        return self._session_factories[name]

    def get_redis(self, name: str) -> Any:
        if name not in self._redis:
            raise KeyError(f"Redis datasource '{name}' not found")
        return self._redis[name]


# Singleton — populated at startup, read throughout the app.
datasources = DatasourceManager()


# ---------- per-request dependency providers ---------------------------------
_sql_provider_cache: dict[str, Callable] = {}
_redis_provider_cache: dict[str, Callable] = {}


def _get_sql_provider(name: str) -> Callable:
    """Return a cached async dependency yielding an AsyncSession for `name`."""
    if name not in _sql_provider_cache:

        async def _provider() -> AsyncIterator[AsyncSession]:
            async with datasources.get_session_factory(name)() as session:
                yield session

        _provider.__name__ = f"get_session_{name}"
        _sql_provider_cache[name] = _provider
    return _sql_provider_cache[name]


def _get_redis_provider(name: str) -> Callable:
    """Return a cached async dependency returning the Redis client for `name`."""
    if name not in _redis_provider_cache:

        async def _provider() -> Any:
            return datasources.get_redis(name)

        _provider.__name__ = f"get_redis_{name}"
        _redis_provider_cache[name] = _provider
    return _redis_provider_cache[name]


# Public convenience: build a Depends() callable for a given datasource.
def get_db(name: str) -> Callable:
    """Dependency callable for SQL datasource `name` (yields AsyncSession)."""
    return _get_sql_provider(name)


def get_redis(name: str) -> Callable:
    """Dependency callable for Redis datasource `name`."""
    return _get_redis_provider(name)


def _make_typed_provider(name: str) -> Callable:
    """延迟解析类型的 provider:运行时根据 datasource 类型决定返回 SQL 还是 Redis。

    为什么需要这个:`@use_datasource("xxx")` 装饰器在模块加载时就执行,
    但那时 datasources 可能还没配置(空配置启动)。把类型解析推迟到请求
    真正进来时,这时 local.yaml 已经加载完了。
    """

    async def _provider() -> Any:
        cfg = settings.datasources.get(name)
        if cfg is None:
            raise RuntimeError(
                f"数据源 '{name}' 未在 config 里配置。"
                f"请在 config/local.yaml 的 datasources 下添加。"
            )
        if cfg.type in ("postgresql", "mysql"):
            async with datasources.get_session_factory(name)() as session:
                yield session
        elif cfg.type == "redis":
            yield datasources.get_redis(name)
        else:
            raise ValueError(f"Unsupported datasource type: {cfg.type}")

    _provider.__name__ = f"use_ds_{name}"
    return _provider


# ---------- Annotated type aliases — generated from config -------------------
# Switching datasource on an endpoint = changing the type annotation.
# NOTE: these are built at import time from the YAML config. Add a new alias
# here for each datasource you want to expose as an annotation.
DbPostgresPrimary = Annotated[AsyncSession, Depends(_get_sql_provider("postgres_primary"))]
DbPostgresReadonly = Annotated[AsyncSession, Depends(_get_sql_provider("postgres_readonly"))]
DbMysqlBusiness = Annotated[AsyncSession, Depends(_get_sql_provider("mysql_business"))]
RedisCache = Annotated[Any, Depends(_get_redis_provider("redis_cache"))]


# ---------- Decorator: @use_datasource (alternative to Annotated) -----------
def use_datasource(name: str, *, alias: str = "db") -> Callable:
    """Inject the chosen datasource as a kwarg dependency.

    Works by rebuilding the wrapped function's signature so FastAPI sees the
    extra `Depends(...)` parameter. SQL sources inject an AsyncSession, Redis
    sources inject the redis client.

    Example:
        @router.get("/items")
        @use_datasource("postgres_primary")
        async def list_items(db: AsyncSession):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    # 不在模块加载时查 settings.datasources[name](否则空配置启动会 KeyError),
    # 改为运行时(provider 真正被调用时)才解析类型。
    # 这里先按 datasource 类型决定注入哪种依赖。
    # 类型可能在 local.yaml 加载后才确定,所以我们用一个延迟解析的 provider。
    dep_default: Any = Depends(_make_typed_provider(name))
    annotation: Any = Any  # 实际类型运行时确定(SQL 或 Redis)

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        params = dict(sig.parameters)

        if alias in params:
            # Handler already declares the parameter (e.g. `db: AsyncSession`).
            # Bind a Depends() default to it so FastAPI injects the chosen ds.
            old = params[alias]
            params[alias] = old.replace(default=dep_default)
        else:
            # Handler does not declare it — add a new keyword-only dependency.
            params[alias] = inspect.Parameter(
                alias,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=dep_default,
                annotation=annotation,
            )
        new_sig = sig.replace(parameters=list(params.values()))

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await func(*args, **kwargs)

            async_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        sync_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        return sync_wrapper

    return decorator


__all__ = [
    "datasources",
    "DatasourceManager",
    "use_datasource",
    "get_db",
    "get_redis",
    # Aliases
    "DbPostgresPrimary",
    "DbPostgresReadonly",
    "DbMysqlBusiness",
    "RedisCache",
]
