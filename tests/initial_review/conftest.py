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


#: 三字段各异的有效文本（≥15 字且互相不相似：A6 同单互比恒 PASS，
#  波次 1 的「三字段同文」默认值在 A6 接线后会同单相似度=1.0 → 假 FAIL）
VALID_REASON = "设备接地引下线锈蚀严重导致接触不良存在安全隐患"
VALID_SHORT = "已更换锈蚀接地扁铁并对接头做紧固和防腐涂刷处理"
VALID_LONG = "建立季度专项巡检制度对接地电阻值定期检测并纳入班组考核"
#: 波次 1 遗留别名（部分测试引用）
VALID_TEXT = VALID_REASON


def make_request(**over: Any) -> RectificationReviewRequest:
    values: dict[str, Any] = {
        "issue_id": "ISS-001",
        "submission_id": "SUB-2026-001",
        "version_no": 1,
        "reason": VALID_REASON,
        "short_term_measure": VALID_SHORT,
        "long_term_measure": VALID_LONG,
    }
    values.update(over)
    return RectificationReviewRequest(**values)


async def insert_exec_row(
    session_factory,
    *,
    issue_id: str = "ISS-002",
    version_no: int = 1,
    status: str = "COMPLETED",
    fingerprint: str = "f" * 64,
    callback_attempts: int = 0,
) -> str:
    """直接落一行执行注册表（不走 submit 链路），供 RUNNING/配额边界测试用。

    task_id/submission_id 按 issue_id 推导，返回 task_id 方便断言。
    """
    from datetime import timedelta

    from app.models.agent_initial_review import AgentInitialReviewExec

    task_id = f"cps-rectify-{issue_id}-v{version_no}"
    now = naive_utc_now()
    async with session_factory() as session, session.begin():
        session.add(
            AgentInitialReviewExec(
                task_id=task_id,
                issue_id=issue_id,
                version_no=version_no,
                submission_id=f"SUB-2026-{issue_id[-3:]}",
                status=status,
                started_at=now,
                deadline_at=now + timedelta(seconds=480),
                fingerprint=fingerprint,
                input_snapshot={},
                callback_attempts=callback_attempts,
            )
        )
    return task_id


__all__ = [
    "VALID_TEXT",
    "FakeCallbackClient",
    "drain",
    "insert_exec_row",
    "make_request",
    "make_service",
    "make_session_factory",
    "make_settings",
    "naive_utc_now",
]
