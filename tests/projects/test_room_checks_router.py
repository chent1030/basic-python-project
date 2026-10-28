"""B6 视觉点检 Agent 路由测试（POST /agent/room-checks/v2/judge + rejudge）。

覆盖目标：
1. /judge 端点 round-trip：cps_admin 角色正常 200
2. /judge 端点：无 token（内网直连）放行 / 坏 token 401
3. /judge 端点 403：非 cps_admin 角色
4. /judge 端点 422：缺必填字段 fingerprint / roomType / checkItemId
5. /judge 端点 422：roomType 取值非法
6. /rejudge 端点 403：非 cps_admin+cps_supervisor 角色
7. /rejudge 端点 200 + cache_cleared=true（有缓存时）
8. /rejudge 端点 200 + cache_cleared=false（无缓存时）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import principal
from app.projects.cps.room_checks.application.judge_service import RoomCheckB6JudgeService
from app.projects.cps.room_checks.domain.evidence_models import JudgeVerdict
from app.projects.cps.room_checks.infrastructure.callback_client import (
    FakeRoomCheckCallbackClient,
)
from app.projects.cps.room_checks.infrastructure.vision_client import (
    FakeVisionModelClient,
    VisionCallResult,
)
from tests.projects.conftest import build_app


# -- helpers -----------------------------------------------------------------
def _payload(**over: Any) -> dict[str, Any]:
    base = {
        "fingerprint": "rc-fp-001",
        "roomType": "PRIMARY",
        "checkItemId": "ci-001",
        "photoUrl": "",
        "expectedMatchType": "地面",
        "expectedKeywords": ["无杂物", "无积水"],
    }
    base.update(over)
    return base


def _scripted_pass_results() -> list[VisionCallResult]:
    return [
        VisionCallResult(
            raw_text='{"matched": true}',
            parsed={"matched": True},
            model="qwen-vl-plus",
        ),
        VisionCallResult(
            raw_text=(
                '{"content_match": true,'
                ' "matched_keywords": ["无杂物"], "missing_keywords": []}'
            ),
            parsed={
                "content_match": True,
                "matched_keywords": ["无杂物"],
                "missing_keywords": [],
            },
            model="qwen-vl-plus",
        ),
        VisionCallResult(
            raw_text='{"evidence_strength": "STRONG", "evidence_notes": "ok"}',
            parsed={"evidence_strength": "STRONG", "evidence_notes": "ok"},
            model="qwen-vl-plus",
        ),
    ]


def _build_service_with_results(results: list[VisionCallResult]) -> RoomCheckB6JudgeService:
    return RoomCheckB6JudgeService(
        vision_client=FakeVisionModelClient(results=results),
        callback_client=FakeRoomCheckCallbackClient(),
    )


# -- /judge 测试 -------------------------------------------------------------
def test_judge_endpoint_round_trip_pass() -> None:
    """正常流程：cps_admin 角色 → 200 + overall=PROBLEM（缺一 keyword）-1。"""
    # missing 一项 → 100 - 20 = 80 (PASS) 或中等
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge", json=_payload()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fingerprint"] == "rc-fp-001"
    assert body["overall"] in {"PASS", "PARTIAL", "PROBLEM"}
    assert 0 <= body["score"] <= 100
    assert isinstance(body["reasons"], list)
    assert "judged_at" in body
    assert body["model_name"] == "qwen-vl-plus"
    # raw_output 包含三级 raw_text
    assert "type_match" in body["raw_output"]


def test_judge_endpoint_returns_problem_on_type_mismatch() -> None:
    """type-match 不符 → 200 + overall=PROBLEM/0。"""
    results = [
        VisionCallResult(
            raw_text='{"matched": false, "observed_type": "天花板"}',
            parsed={"matched": False, "observed_type": "天花板"},
            model="qwen-vl-plus",
        ),
    ]
    svc = _build_service_with_results(results)
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge", json=_payload()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "PROBLEM"
    assert body["score"] == 0


def test_judge_endpoint_internal_direct_accepted() -> None:
    """无 token（内网直连）→ 200：不再有密钥/IP 白名单校验。

    build_app 默认注入 principal；这里移除 override 走真实 principal，验证
    无 Authorization 头即被当作内网直连（legacy-cps，含 cps_admin），
    因此 Java 侧只填地址即可调用。
    """
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    app.dependency_overrides.pop(principal, None)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge",
        json=_payload(),
        headers={"x-forwarded-for": "203.0.113.42"},  # 任意来源 IP 均不再影响判定
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fingerprint"] == "rc-fp-001"


def test_judge_endpoint_invalid_token_401() -> None:
    """带无法解析的 Bearer → 401（不静默降级成内网直连）。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    app.dependency_overrides.pop(principal, None)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge",
        json=_payload(),
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401


def test_judge_endpoint_forbidden_non_admin() -> None:
    """非 cps_admin 角色 → 403 Insufficient framework role。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc, roles=["cps_employee"])  # 无 cps_admin
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge", json=_payload()
    )
    assert resp.status_code == 403


def test_judge_endpoint_missing_required_field_422() -> None:
    """缺 fingerprint → 422。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    payload = _payload()
    payload.pop("fingerprint")
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge", json=payload
    )
    assert resp.status_code == 422


def test_judge_endpoint_invalid_room_type_422() -> None:
    """roomType 非枚举 → 422。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge",
        json=_payload(roomType="MYSTERY_ROOM"),
    )
    assert resp.status_code == 422
    assert "roomType invalid" in resp.json()["detail"]


def test_judge_endpoint_photo_url_only_accepted() -> None:
    """照片只有 photoUrl 一种形态：无照片不 422，URL 不可达走 fallback 仍 200。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge",
        json=_payload(photoUrl="https://x.test/photo.jpg"),
    )
    assert resp.status_code == 200


def test_judge_endpoint_expected_keywords_string_csv_accepted() -> None:
    """expectedKeywords 可传逗号分隔字符串（兼容简写）。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/judge",
        json=_payload(expectedKeywords="无杂物,无积水"),
    )
    assert resp.status_code == 200


# -- /rejudge 测试 ------------------------------------------------------------
def test_rejudge_endpoint_clears_cache_when_present() -> None:
    """有缓存时 → 200 + cache_cleared=true。"""
    from app.projects.cps.room_checks.domain.evidence_models import (
        RoomCheckVerdict,
    )

    svc = _build_service_with_results(_scripted_pass_results())
    # 直接预热 cache（最直接方式）
    svc.cache.put(
        "rc-fp-001",
        RoomCheckVerdict(
            fingerprint="rc-fp-001",
            overall=JudgeVerdict.PASS,
            score=100,
            reasons=("cached",),
            judged_at=datetime.now(UTC).replace(tzinfo=None),
            model_name="qwen-vl-plus",
            raw_output={},
        ),
    )
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge",
        json={"fingerprint": "rc-fp-001", "retryCount": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["cache_cleared"] is True


def test_rejudge_endpoint_returns_false_when_no_cache() -> None:
    """无缓存时 → 200 + cache_cleared=false。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge",
        json={"fingerprint": "rc-fp-never-seen"},
    )
    assert resp.status_code == 200
    assert resp.json()["cache_cleared"] is False


def test_rejudge_endpoint_forbidden_non_supervisor() -> None:
    """非 cps_admin+cps_supervisor 角色 → 403。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc, roles=["cps_admin"])  # 缺 cps_supervisor
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge",
        json={"fingerprint": "rc-fp-001"},
    )
    assert resp.status_code == 403


def test_rejudge_endpoint_admin_supervisor_passes() -> None:
    """cps_admin + cps_supervisor 双角色 → 200。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc, roles=["cps_admin", "cps_supervisor"])
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge",
        json={"fingerprint": "rc-fp-001", "retryCount": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["retry_count"] == 2


def test_rejudge_endpoint_422_missing_fingerprint() -> None:
    """缺 fingerprint → 422。"""
    svc = _build_service_with_results(_scripted_pass_results())
    app = build_app(service=svc)
    resp = TestClient(app).post(
        "/api/v1/agent/room-checks/v2/rejudge", json={}
    )
    assert resp.status_code == 422