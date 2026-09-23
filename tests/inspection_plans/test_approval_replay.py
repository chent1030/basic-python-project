"""C7 端到端装配 — 巡检计划草稿→批准回调幂等（C-07 语义链）。

测试金字塔标注：**集成级**（真实服务 + FastAPI 路由，进程内缓存替身即可）。

链路（PRD §22 / AC-06/30）：Java applyDraft → Python C-07 草稿 → 人工批准 →
Java 落库建单。批准环节 Java 可能重取草稿（同 idempotency_key 重放）——
Python 侧必须返回**同一个 draft_id + 逐字节相同的内容**，否则批准的是
一份草稿、建单用的是另一份。重放以 status=REPLAYED 显式标识。

无独立「批准回调」端点（Python 只出草稿；批准事务在 Java D3），
本文件锁定 Python 侧可测的幂等语义。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.agent_runs import Principal, principal
from app.api.v1.endpoints.inspection_plans import router as plans_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(plans_router, prefix="/api/v1")
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=["cps_admin"], expires=time.time() + 3600
    )
    return app


def _payload(**overrides: Any) -> dict[str, Any]:
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


def _draft(app: FastAPI, payload: dict) -> Any:
    return TestClient(app).post("/api/v1/agent/inspection-plans/draft", json=payload)


def test_approval_flow_replay_returns_identical_draft() -> None:
    """批准流程重取：同 key 三次请求 draft_id / 内容逐字段相同，仅 status 翻转。"""
    app = _build_app()
    payload = _payload(idempotency_key="plan-draft-weekly-RECTIFY-20260920")

    first = _draft(app, payload).json()
    # 模拟批准后 Java 重取（审批页刷新 / 建单前确认两次）
    second = _draft(app, payload).json()
    third = _draft(app, payload).json()

    assert first["status"] == "DRAFT"
    assert second["status"] == third["status"] == "REPLAYED"
    assert first["draft_id"] == second["draft_id"] == third["draft_id"]
    content = first["draft_content_json"]
    assert content == second["draft_content_json"] == third["draft_content_json"]
    assert first["generated_at"] == second["generated_at"]  # 原生成时刻不更新
    assert first["model_version"] == second["model_version"] == "plan-draft/skill@1"


def test_approval_flow_different_key_new_draft() -> None:
    """不同来源（另一周报 run）取草稿 → 新 draft_id，互不串单。"""
    app = _build_app()
    a = _draft(app, _payload(idempotency_key="plan-draft-run-A")).json()
    b = _draft(app, _payload(idempotency_key="plan-draft-run-B")).json()

    assert a["draft_id"] != b["draft_id"]
    assert a["status"] == b["status"] == "DRAFT"
    assert a["source_run_id"] == b["source_run_id"]  # 载荷同源但幂等键不同


def test_approval_flow_derived_key_replay_stable() -> None:
    """缺省幂等键按 source_run_id 派生（Java 约定 plan-draft-{sourceRunId}）：
    未显式传 key 的重放同样命中缓存。"""
    app = _build_app()
    first = _draft(app, _payload()).json()  # 缺省派生
    second = _draft(app, _payload()).json()
    assert second["status"] == "REPLAYED"
    assert first["draft_id"] == second["draft_id"]


def test_blueprint_task_types_by_plan_type() -> None:
    """三类计划蓝图的 task_type/priority 权重口径（PRD §22 三类任务）。

    蓝图恒含三类 INSPECT_* 任务；与 plan_type 同名的任务为 high
    （主任务），其余为伴随任务。
    """
    app = _build_app()
    suffix = {"RECTIFY", "PATROL", "CHECK"}
    for plan_type in ("WEEKLY_RECTIFY", "WEEKLY_PATROL", "WEEKLY_CHECK"):
        # 每类型独立幂等键（否则命中同 source_run_id 派生缓存拿到第一份草稿）
        payload = _payload(plan_type=plan_type, idempotency_key=f"bp-{plan_type}")
        data = _draft(app, payload).json()
        tasks = data["draft_content_json"]["tasks"]
        types = {t["task_type"] for t in tasks}
        assert types == {f"INSPECT_{s}" for s in suffix}, plan_type
        main = f"INSPECT_{plan_type.removeprefix('WEEKLY_')}"
        prio = {t["task_type"]: t["priority"] for t in tasks}
        assert prio[main] == "high", (plan_type, prio)
        assert len(tasks) == 3
