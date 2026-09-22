"""initial_review 服务/路由测试基建。

要点：
- SQLite 内存库 + Base.metadata.create_all（JSONVariant 在 sqlite 退化为通用 JSON）；
- 时钟注入：sqlite 无时区，用**朴素 UTC** datetime（生产 PG 是 timestamptz，感知型）；
- 回调/分发器注入：FakeCallbackClient 记录 payload；dispatcher 把协程收进 pending
  列表由测试显式 await（确定性，不依赖后台任务调度）。
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # 注册全部 ORM 到 Base.metadata
from app.db.base import Base
from app.projects.initial_review.application.service import InitialReviewService
from app.projects.initial_review.domain.models import RectificationReviewRequest
from app.projects.initial_review.infrastructure.config import InitialReviewSettings


def naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_settings(**over: Any) -> InitialReviewSettings:
    values: dict[str, Any] = {
        "deadline_seconds": 480.0,
        "java_callback_base": "http://java.test",
        "java_callback_path": "/api/callbacks/initial-review/result",
        "callback_timeout_seconds": 1.0,
        "callback_max_retries": 2,
        "callback_backoff_seconds": (),
    }
    values.update(over)
    return InitialReviewSettings(**values)


class FakeCallbackClient:
    """记录 payload；前 N 次 send 失败。接口对齐 JavaCallbackClient.send 的新签名。"""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.payloads: list[dict] = []

    async def send(self, payload: dict) -> tuple[bool, int, str | None]:
        self.payloads.append(payload)
        if len(self.payloads) <= self.failures:
            return False, 1, f"simulated failure #{len(self.payloads)}"
        return True, 1, None


async def make_session_factory() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


def make_service(
    session_factory,
    *,
    settings: InitialReviewSettings | None = None,
    callback: FakeCallbackClient | None = None,
    clock=None,
    check_runner=None,
) -> tuple[InitialReviewService, FakeCallbackClient, list[Awaitable]]:
    settings = settings or make_settings()
    callback = callback or FakeCallbackClient()
    pending: list[Awaitable] = []
    service = InitialReviewService(
        session_factory,
        callback,  # type: ignore[arg-type]  # 测试替身，接口兼容
        settings,
        clock=clock or naive_utc_now,
        check_runner=check_runner,
        callback_dispatcher=lambda coro: pending.append(coro),
    )
    return service, callback, pending


async def drain(pending: list[Awaitable]) -> None:
    while pending:
        await pending.pop(0)


VALID_TEXT = "整改原因整改原因整改原因整改原"  # 恰 15 字（4×3+3）


def make_request(**over: Any) -> RectificationReviewRequest:
    values: dict[str, Any] = {
        "issue_id": "ISS-001",
        "submission_id": "SUB-2026-001",
        "version_no": 1,
        "reason": VALID_TEXT,
        "short_term_measure": VALID_TEXT,
        "long_term_measure": VALID_TEXT,
    }
    values.update(over)
    return RectificationReviewRequest(**values)


__all__ = [
    "VALID_TEXT",
    "FakeCallbackClient",
    "drain",
    "make_request",
    "make_service",
    "make_session_factory",
    "make_settings",
    "naive_utc_now",
]
