"""Alembic 运行环境。

数据库连接从 app.core.config.settings 读取(不使用 alembic.ini 的 sqlalchemy.url),
这样:
- 复用 config.yaml 的数据源配置(包括密码加密)
- 不需要单独维护 alembic.ini 里的 URL

选择迁移哪个数据源:
    alembic upgrade head                          # 默认 postgres_primary
    alembic -x datasource=mysql_business upgrade head
    alembic -x datasource=postgres_readonly upgrade head

Alembic 是同步的,但我们用的是 async DSN(asyncpg/aiomysql),
所以这里把驱动前缀换成同步驱动(psycopg2/pymysql)。
"""
from __future__ import annotations

import logging
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 重要:导入所有 models,确保 Base.metadata 包含全部表定义
import app.models  # noqa: F401

# 项目导入:必须在 alembic.ini 的 prepend_sys_path 之后可用
from app.core.config import settings
from app.db.base import Base

config = context.config

# 日志配置(来自 alembic.ini)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# --------------------------------------------------------------------------
# 从 settings 解析目标数据源 + DSN
# --------------------------------------------------------------------------
def _resolve_target_datasource() -> str:
    """通过 alembic -x datasource=xxx 选择数据源,默认 postgres_primary。"""
    cmd_args = context.get_x_argument(as_dictionary=True)
    return cmd_args.get("datasource", settings.alembic.default_datasource)


def _to_sync_dsn(async_dsn: str) -> str:
    """把 async DSN 转成同步 DSN(Alembic 走同步路径)。

    asyncpg -> psycopg2,aiomysql -> pymysql。本地测试的 aiosqlite -> sqlite。
    """
    return (
        async_dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


target_ds_name = _resolve_target_datasource()
ds_cfg = settings.datasources.get(target_ds_name)
if ds_cfg is None or not ds_cfg.is_configured():
    raise SystemExit(
        f"数据源 '{target_ds_name}' 未配置。"
        f"请在 config/local.yaml 的 datasources 下添加,或用 "
        f"alembic -x datasource=<name> 指定其他数据源。"
    )

# Alembic 用同步 DSN
config.set_main_option("sqlalchemy.url", _to_sync_dsn(ds_cfg.dsn))

# Alembic 操作的 metadata —— 所有 ORM 模型都注册到这里
target_metadata = Base.metadata


def _mask_password(dsn: str) -> str:
    """日志里隐藏密码。"""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)


log = logging.getLogger("alembic.env")
log.info(
    "Alembic 使用数据源 '%s',URL(隐藏密码): %s",
    target_ds_name,
    _mask_password(ds_cfg.dsn),
)


# --------------------------------------------------------------------------
# offline 模式:生成 SQL 脚本,不连数据库
# --------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# --------------------------------------------------------------------------
# online 模式:连数据库执行
# --------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
