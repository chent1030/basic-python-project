"""B6 视觉点检 Agent 测试基建（复用 tests/initial_review/conftest 模式）。

要点：
- 时钟：测试用朴素 UTC（与 sqlite 测试同源；生产 PG 感知型不冲突）；
- 视觉模型替身：``FakeVisionModelClient`` 按脚本返回 VisionCallResult；
- 回调替身：``FakeRoomCheckCallbackClient`` 记录 dispatches；
- 端点 principal override：与 tests/room_checks/test_endpoint 同款。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal
from app.projects.room_checks.api.router import router as b6_router
from app.projects.room_checks.application.judge_service import RoomCheckB6JudgeService


def naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_evidence(**over: Any):
    """构造最小可用的 RoomCheckEvidence 默认值。"""
    from datetime import datetime

    from app.projects.room_checks.domain.evidence_models import (
        RoomCheckEvidence,
        RoomType,
    )

    base: dict[str, Any] = {
        "fingerprint": "rc-fp-001",
        "room_type": RoomType.PRIMARY,
        "check_item_id": "ci-001",
        "photo_object_key": "room/101/ground-1.jpg",
        "photo_url": None,
        "expected_match_type": "地面",
        "expected_keywords": ("无杂物", "无积水"),
        "submitted_at": datetime.now(UTC).replace(tzinfo=None),
    }
    base.update(over)
    return RoomCheckEvidence(**base)


def build_app(
    *,
    service: RoomCheckB6JudgeService | None = None,
    roles: list[str] | None = None,
) -> FastAPI:
    """构建最小 FastAPI app（只挂 B6 路由；不触发 main.py 的全局副作用）。"""
    app = FastAPI()
    app.include_router(b6_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin", "cps_supervisor"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="local-factory",
        actor="java-test",
        roles=chosen,
        expires=time.time() + 3600,
    )
    if service is not None:
        app.state.room_check_b6_judge_service = service
    return app


def post_judge(app: FastAPI, payload: dict[str, Any]) -> Any:
    return TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge", json=payload
    )


def post_rejudge(app: FastAPI, payload: dict[str, Any]) -> Any:
    return TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge", json=payload
    )