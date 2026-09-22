"""周报测试 fixtures（沿用 tests.initial_review.conftest 的风格）。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from app.models.weekly_report import WeeklyReportRun
from app.projects.initial_review.infrastructure.config import (
    RustFSSettings,
    load_initial_review_settings,
)
from app.projects.weekly_report.application.service import WeeklyReportService
from app.projects.weekly_report.domain.data_fetcher import StubDataFetcher
from app.projects.weekly_report.infrastructure.config import (
    WeeklyReportRustFSSettings,
    WeeklyReportSettings,
)
from app.projects.weekly_report.infrastructure.pusher import NoOpPusher
from app.projects.weekly_report.infrastructure.repository import (
    WeeklyReportRepository,
)
from app.projects.weekly_report.infrastructure.rustfs_uploader import (
    RustFSWeeklyReportUploader,
)
from app.skills.weekly_report.skill import build_default_skill


def _naive_utc_now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def fixed_now() -> datetime:
    """2026-W18 Mon 08:00 Asia/Shanghai = 2026-05-04T00:00:00Z."""
    return datetime(2026, 5, 4, 8, 0, 0, tzinfo=UTC)


def make_weekly_report_settings() -> WeeklyReportSettings:
    """sqlite-test-friendly 设置：RustFS 标记为 unavailable（archiver 不可达）。"""
    irs = load_initial_review_settings()
    return WeeklyReportSettings(
        sched_enabled=False,
        report_types=("cps-issue-inspection", "room-check"),
        rustfs=WeeklyReportRustFSSettings(
            endpoint=irs.rustfs.endpoint,
            access_key=irs.rustfs.access_key,
            secret_key=irs.rustfs.secret_key,
            bucket=irs.rustfs.bucket,
        ),
    )


@pytest_asyncio.fixture
async def make_session_factory():
    """sqlite in-memory + aiosqlite (async sessionmaker factory)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.base import Base
    # 显式建表（绕开 alembic；测试环境跑 sqlite）
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield session_factory
    await engine.dispose()


@pytest.fixture
def make_service(
    make_session_factory: Callable,
) -> Callable[..., WeeklyReportService]:
    def _make(**overrides: Any) -> WeeklyReportService:
        settings = make_weekly_report_settings()
        kwargs: dict[str, Any] = {
            "session_factory": make_session_factory,
            "data_fetcher": StubDataFetcher(now=_naive_utc_now()),
            "skill": build_default_skill(),
            "uploader": RustFSWeeklyReportUploader(settings.rustfs),
            "pusher": NoOpPusher(),
            "settings": settings,
            "repository": WeeklyReportRepository(),
            "clock": _naive_utc_now,
        }
        kwargs.update(overrides)
        svc = WeeklyReportService(**kwargs)
        return svc

    return _make


@pytest.fixture
def fake_uploader():
    """占位 uploader；本波次测试不验 RustFS（详见 test_rustfs_signing.py 后续补）。"""
    return None


__all__ = [
    "WeeklyReportRun",
    "fixed_now",
    "make_service",
    "make_session_factory",
    "make_weekly_report_settings",
]


_ = RustFSSettings  # noqa: F841  # 占位防 ruff 静态分析误报