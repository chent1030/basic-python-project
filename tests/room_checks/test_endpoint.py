"""C-04 端点契约测试 — 通过 /api/v1/agent/room-checks/judge 投递。

复用 inspection_plans/test_endpoint 的 principal override 模式；
服务经 app.state.room_check_judge_service 注入（端点惰性装配同款）。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal
from app.api.v1.endpoints.room_checks import router as room_checks_router
from app.projects.initial_review.infrastructure.model_client import FakeModelCheckClient
from app.projects.room_checks.application.service import (
    JudgeRequestError,
    JudgeTechnicalError,
    RoomCheckJudgeService,
)
from app.projects.room_checks.domain.models import ContentJudgeSchema, TypeMatchSchema
from tests.room_checks.test_service import FakeFetcher, make_settings

PASS_RESULTS = [
    TypeMatchSchema(type_match=True, photo_subject="地面", reason="主体为地面"),
    ContentJudgeSchema(verdict="PASS", reason="地面整洁", evidence="无杂物", confidence=0.9),
]


def _build_app(
    roles: list[str] | None = None, service: Any | None = None
) -> FastAPI:
    app = FastAPI()
    app.include_router(room_checks_router, prefix="/api/v1")
    chosen = roles if roles is not None else ["cps_admin"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600,
    )
    if service is not None:
        app.state.room_check_judge_service = service
    return app


def _make_service(results: list | None = None) -> RoomCheckJudgeService:
    return RoomCheckJudgeService(
        model_client=FakeModelCheckClient(results=list(results or PASS_RESULTS)),
        fetcher=FakeFetcher(),
        settings=make_settings(),
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "submission_id": "sub-1",
        "item_id": "item-9",
        "attempt": 1,
        "item_content": "地面无杂物、无积水",
        "item_type": "地面",
        "photo_object_keys": ["room/101/ground-1.jpg"],
        "deduction": 10,
        "config_version": "v3",
        "room_name": "1号楼配电间",
    }
    base.update(overrides)
    return base


def _post(app: FastAPI, payload: dict) -> Any:
    return TestClient(app).post("/api/v1/agent/room-checks/judge", json=payload)


def test_endpoint_judged_pass_contract() -> None:
    app = _build_app(service=_make_service())
    resp = _post(app, _payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "JUDGED"
    assert body["verdict"] == "PASS"
    assert body["reason"] == "地面整洁"
    assert body["photo_object_keys"] == ["room/101/ground-1.jpg"]
    assert body["item_snapshot"]["config_version"] == "v3"
    assert body["stage_trace"]["type_match"]["status"] == "PASS"
    assert body["model_version"] == "room-judge/qwen-vl@1"
    assert body["replayed"] is False
    # 缺省幂等键按契约派生
    assert body["idempotency_key"] == "room-judge-sub-1-item-9-1"


def test_endpoint_idempotent_replay() -> None:
    app = _build_app(service=_make_service())
    first = _post(app, _payload()).json()
    second = _post(app, _payload()).json()
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["idempotency_key"] == second["idempotency_key"]


def test_endpoint_type_mismatch_flow() -> None:
    service = _make_service(results=[
        TypeMatchSchema(type_match=False, photo_subject="桌面", reason="拍的是桌面"),
    ])
    app = _build_app(service=service)
    body = _post(app, _payload()).json()
    assert body["status"] == "TYPE_MISMATCH"
    assert body["verdict"] is None
    assert body["stage_trace"]["content_judge"]["status"] == "NOT_REACHED"


def test_endpoint_502_on_technical_failure() -> None:
    class ExplodingService:
        max_photos = 4

        async def judge(self, req):  # type: ignore[no-untyped-def]
            raise JudgeTechnicalError("类型匹配阶段失败：模型调用异常")

    app = _build_app(service=ExplodingService())
    resp = _post(app, _payload())
    assert resp.status_code == 502
    assert resp.json()["detail"]["error_code"] == "ROOM_JUDGE_MODEL_FAILED"


def test_endpoint_422_on_request_error_from_service() -> None:
    class OverBudgetService:
        max_photos = 4

        async def judge(self, req):  # type: ignore[no-untyped-def]
            raise JudgeRequestError("照片数量 5 超过模型预算上限 4")

    app = _build_app(service=OverBudgetService())
    resp = _post(app, _payload(photo_object_keys=[f"p{i}.jpg" for i in range(5)]))
    assert resp.status_code == 422


def test_endpoint_422_missing_or_invalid_fields() -> None:
    app = _build_app(service=_make_service())
    assert _post(app, _payload(item_content=None)).status_code == 422
    assert _post(app, _payload(item_type="  ")).status_code == 422
    assert _post(app, _payload(photo_object_keys=[])).status_code == 422
    assert _post(app, _payload(photo_object_keys="room/1.jpg")).status_code == 422
    assert _post(app, _payload(attempt=0)).status_code == 422
    assert _post(app, _payload(attempt="1")).status_code == 422
    assert _post(app, _payload(deduction="ten")).status_code == 422
    missing = _payload()
    del missing["item_id"]
    assert _post(app, missing).status_code == 422


def test_endpoint_403_without_cps_admin_role() -> None:
    app = _build_app(roles=["cps_employee"], service=_make_service())
    resp = _post(app, _payload())
    assert resp.status_code == 403
