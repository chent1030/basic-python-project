"""C7 契约 schema 冻结测试 — C-01/03/04/06/07 请求响应字段集固化
（C-05/C-08 见 tests/weekly_report/test_e2e_assembly.py，同链路内联冻结）。

测试金字塔标注：本文件全部为**集成级**测试（FastAPI 路由 + 测试替身服务，
无网络、无真实模型/存储）。

目的（主计划 C7）：防止字段漂移 —— 任何端点响应新增/删除/改名字段，
对应断言立即失败，强制契约变更显式化（先改契约文档再改代码）。

冻结口径来源：docs/波次1-5 设计文档契约定义 + 现行端点实现。
每条 EXPECTED_* 集合即当前生产契约的**精确**字段集（无多余/缺失）。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal

# ======================================================================
# 现行契约字段集（冻结基线 — 变更需同步改这里 + 契约文档 + Java 侧）
# ======================================================================

#: C-01 POST /agent/rectifications → 202 响应体（首建与重放同构）
C01_RESPONSE_KEYS = {
    "review_task_ref",  # Java 幂等对账键 cps-rectify-{issueId}-v{n}
    "task_id",          # 与 review_task_ref 同值（历史别名，勿删）
    "status",           # RUNNING/COMPLETED/FAILED
    "overall",          # PASS/PARTIAL/PROBLEM（COMPLETED 时）
    "deadline_at",      # wall-clock 截止 ISO 时间
    "replayed",         # 幂等重放标志
}

#: C-03 GET /agent/rectifications/{task_id} → 200 响应体
C03_RESPONSE_KEYS = {
    "task_id",
    "review_task_ref",
    "issue_id",
    "version_no",
    "submission_id",
    "status",
    "overall",
    "model_version",
    "error",
    "error_code",
    "started_at",
    "finished_at",
    "deadline_at",
    "text_checks",       # A5 确定性规则结果（三字段）
    "model_checks",      # A6/A7/A8 模型检查结果
    "callback_status",   # PENDING/SUCCESS/FAILED（Java 兜底轮询依据）
}

#: C-04 POST /agent/room-checks/judge → 200 响应体
C04_RESPONSE_KEYS = {
    "idempotency_key",
    "submission_id",
    "item_id",
    "attempt",
    "status",            # JUDGED/SKIPPED
    "verdict",           # PASS/WARN/FAIL/TYPE_MISMATCH/CANNOT_JUDGE
    "reason",
    "evidence",
    "photo_object_keys",
    "item_snapshot",
    "stage_trace",       # 两段式判定轨迹
    "model_version",
    "prompt_versions",
    "judged_at",
    "replayed",
}

#: C-06 POST /agent/speech-to-text → 200 响应体
C06_RESPONSE_KEYS = {
    "idempotency_key",
    "request_id",        # idempotency_key 别名（语音侧历史口径）
    "submission_id",
    "field",             # reason/short_term_measure/long_term_measure
    "attempt",
    "status",            # TRANSCRIBED/SKIPPED
    "text",
    "confidence",
    "duration_seconds",
    "language",
    "model",
    "audio_object_key",
    "metadata",
    "transcribed_at",
    "replayed",
}

#: C-07 POST /agent/inspection-plans/draft → 200 响应体
C07_RESPONSE_KEYS = {
    "draft_id",
    "plan_type",
    "title",
    "source_run_id",
    "draft_content_json",  # 内嵌 schema 见 test_c07_draft_content_schema
    "generated_at",
    "model_version",
    "status",              # DRAFT/REPLAYED
}

#: FastAPI HTTPException 错误体（所有端点错误路径统一）
ERROR_BODY_KEYS = {"detail"}


def _principal_override(app: FastAPI, roles: list[str] | None = None) -> None:
    chosen = roles if roles is not None else ["cps_admin"]
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=chosen, expires=time.time() + 3600
    )


def _get(app: FastAPI, path: str) -> Any:
    return TestClient(app).get(path)


def _post(app: FastAPI, path: str, json: dict | None = None) -> Any:
    return TestClient(app).post(path, json=json)


# ======================================================================
# C-01 / C-03（initial_review，复用 sqlite 服务基建）
# ======================================================================


async def _c01_client() -> FastAPI:
    from app.api.v1.endpoints.agent_callbacks import initial_review_service
    from app.api.v1.endpoints.agent_callbacks import router as agent_router
    from tests.initial_review.conftest import make_request, make_service, make_session_factory

    sf = await make_session_factory()
    service, _, _ = make_service(sf)
    app = FastAPI()
    app.include_router(agent_router, prefix="/api/v1")
    _principal_override(app)
    app.dependency_overrides[initial_review_service] = lambda: service
    return app, make_request


async def test_c01_response_schema_frozen() -> None:
    app, make_request = await _c01_client()
    resp = _post(app, "/api/v1/agent/rectifications", json=make_request().model_dump(mode="json"))
    assert resp.status_code == 202
    data = resp.json()
    assert set(data) == C01_RESPONSE_KEYS
    assert isinstance(data["replayed"], bool)
    assert isinstance(data["review_task_ref"], str)
    assert isinstance(data["deadline_at"], str)


async def test_c01_replay_response_schema_identical() -> None:
    """重放响应与首建响应字段集完全一致（Java 侧同一 DTO 反序列化）。"""
    app, make_request = await _c01_client()
    body = make_request().model_dump(mode="json")
    first = _post(app, "/api/v1/agent/rectifications", json=body)
    second = _post(app, "/api/v1/agent/rectifications", json=body)
    assert set(first.json()) == set(second.json()) == C01_RESPONSE_KEYS


async def test_c01_conflict_error_body_frozen() -> None:
    app, make_request = await _c01_client()
    from tests.initial_review.conftest import VALID_TEXT

    body = make_request().model_dump(mode="json")
    _post(app, "/api/v1/agent/rectifications", json=body)
    resp = _post(
        app,
        "/api/v1/agent/rectifications",
        json=make_request(reason="不同参数必须冲突" + VALID_TEXT).model_dump(mode="json"),
    )
    assert resp.status_code == 409
    assert set(resp.json()) == ERROR_BODY_KEYS


async def test_c03_response_schema_frozen() -> None:
    app, make_request = await _c01_client()
    _post(app, "/api/v1/agent/rectifications", json=make_request().model_dump(mode="json"))
    resp = _get(app, "/api/v1/agent/rectifications/cps-rectify-ISS-001-v1")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == C03_RESPONSE_KEYS
    # text_checks 三字段独立结构固化（短键名：reason/short_term/long_term）
    assert set(data["text_checks"]) == {
        "reason", "short_term", "long_term",
    }
    assert data["callback_status"] in {"PENDING", "SUCCESS", "FAILED"}


async def test_c03_not_found_error_body_frozen() -> None:
    app, _ = await _c01_client()
    resp = _get(app, "/api/v1/agent/rectifications/cps-rectify-NOPE-v1")
    assert resp.status_code == 404
    assert set(resp.json()) == ERROR_BODY_KEYS


# ======================================================================
# C-04（room_checks）
# ======================================================================


def _c04_app() -> FastAPI:
    from app.api.v1.endpoints.room_checks import router as room_router
    from tests.room_checks.test_endpoint import PASS_RESULTS, _make_service, _payload

    app = FastAPI()
    app.include_router(room_router, prefix="/api/v1")
    _principal_override(app)
    app.state.room_check_judge_service = _make_service(PASS_RESULTS)
    return app, _payload


def test_c04_response_schema_frozen() -> None:
    app, _payload = _c04_app()
    resp = _post(app, "/api/v1/agent/room-checks/judge", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == C04_RESPONSE_KEYS
    assert isinstance(data["stage_trace"], dict)  # {type_match:…, content_judge:…}
    assert set(data["stage_trace"]) == {"type_match", "content_judge"}
    assert isinstance(data["prompt_versions"], dict)


def test_c04_technical_error_body_frozen() -> None:
    """502 技术失败体：detail 内嵌 error_code/message（B6 口径）。"""
    from app.api.v1.endpoints.room_checks import router as room_router
    from app.projects.room_checks.application.service import (
        JudgeTechnicalError,
        RoomCheckJudgeService,
    )
    from tests.room_checks.test_endpoint import _payload

    class _FailingService(RoomCheckJudgeService):
        async def judge(self, req):  # type: ignore[override,no-untyped-def]
            raise JudgeTechnicalError("model down")

    app = FastAPI()
    app.include_router(room_router, prefix="/api/v1")
    _principal_override(app)
    app.state.room_check_judge_service = _FailingService(
        model_client=None, fetcher=None, settings=None  # type: ignore[arg-type]
    )
    resp = _post(app, "/api/v1/agent/room-checks/judge", json=_payload())
    assert resp.status_code == 502
    body = resp.json()
    assert set(body) == ERROR_BODY_KEYS
    assert set(body["detail"]) == {"error_code", "message"}


# ======================================================================
# C-06（speech）
# ======================================================================


def _c06_app() -> tuple[FastAPI, Any]:
    import base64

    from app.api.v1.endpoints.speech import router as speech_router
    from app.projects.speech.application.service import SpeechToTextService
    from app.projects.speech.infrastructure.asr_client import (
        AsrTranscription,
        FakeAsrClient,
    )
    from tests.speech.helpers import FakeAudioFetcher, make_settings

    ok = AsrTranscription(
        text="泵房地面有积水，需要清理",
        model="qwen3-asr-flash",
        duration_seconds=8.0,
        language="zh",
        raw={"request_id": "asr-1"},
    )
    svc = SpeechToTextService(
        settings=make_settings(),
        asr_client=FakeAsrClient(results=[ok]),
        audio_fetcher=FakeAudioFetcher(),
    )
    app = FastAPI()
    app.include_router(speech_router, prefix="/api/v1")
    _principal_override(app)
    app.state.speech_to_text_service = svc

    def _payload(**overrides: Any) -> dict[str, Any]:
        base = {
            "submission_id": "sub-1",
            "field": "reason",
            "attempt": 1,
            "audio_base64": base64.b64encode(b"fake-audio").decode("ascii"),
        }
        base.update(overrides)
        return base

    return app, _payload


def test_c06_response_schema_frozen() -> None:
    app, _payload = _c06_app()
    resp = _post(app, "/api/v1/agent/speech-to-text", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == C06_RESPONSE_KEYS
    assert data["request_id"] == data["idempotency_key"]  # 别名回显口径
    assert isinstance(data["metadata"], dict)


def test_c06_replay_response_schema_identical() -> None:
    app, _payload = _c06_app()
    first = _post(app, "/api/v1/agent/speech-to-text", json=_payload())
    second = _post(app, "/api/v1/agent/speech-to-text", json=_payload())
    assert set(first.json()) == set(second.json()) == C06_RESPONSE_KEYS
    assert second.json()["replayed"] is True


# ======================================================================
# C-07（inspection_plans）
# ======================================================================


def _c07_post(app: FastAPI, payload: dict) -> Any:
    return _post(app, "/api/v1/agent/inspection-plans/draft", json=payload)


def _c07_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "source_run_id": "weekly-RECTIFY-20260920",
        "plan_type": "WEEKLY_RECTIFY",
        "title": "9月第3周整改复查",
        "factory": "F1",
        "area": "A1",
        "risk_basis": "周报指向重复发生",
    }
    base.update(overrides)
    return base


def test_c07_response_schema_frozen() -> None:
    from app.api.v1.endpoints.inspection_plans import router as plans_router

    app = FastAPI()
    app.include_router(plans_router, prefix="/api/v1")
    _principal_override(app)
    resp = _c07_post(app, _c07_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == C07_RESPONSE_KEYS
    assert data["status"] == "DRAFT"


def test_c07_draft_content_schema_frozen() -> None:
    """draft_content_json 内嵌结构冻结（plan-draft.v1，D1 扩展点前后兼容锚）。"""
    from app.api.v1.endpoints.inspection_plans import router as plans_router

    app = FastAPI()
    app.include_router(plans_router, prefix="/api/v1")
    _principal_override(app)
    data = _c07_post(app, _c07_payload()).json()
    content = data["draft_content_json"]
    assert set(content) == {"schema_version", "tasks", "context"}
    assert content["schema_version"] == "plan-draft.v1"
    assert set(content["context"]) == {"factory", "area", "risk_basis"}
    for task in content["tasks"]:
        assert set(task) == {"task_type", "priority", "rationale"}
    assert {t["priority"] for t in content["tasks"]} <= {"high", "normal", "low"}


def test_c07_replay_response_schema_identical() -> None:
    """同 key 重放（Java 批准流程再取草稿）字段集一致，仅 status 翻转 REPLAYED。"""
    from app.api.v1.endpoints.inspection_plans import router as plans_router

    app = FastAPI()
    app.include_router(plans_router, prefix="/api/v1")
    _principal_override(app)
    payload = _c07_payload(idempotency_key="plan-draft-run-1")
    first = _c07_post(app, payload)
    second = _c07_post(app, payload)
    assert set(first.json()) == set(second.json()) == C07_RESPONSE_KEYS
    assert first.json()["status"] == "DRAFT"
    assert second.json()["status"] == "REPLAYED"
    assert first.json()["draft_id"] == second.json()["draft_id"]
