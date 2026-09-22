"""C-07 服务单元测试 — 任务蓝图 / 幂等重放 / 草稿结构。"""

from __future__ import annotations

from app.projects.inspection_plans.application.service import (
    DRAFT_MODEL_VERSION,
    TASK_TYPE_CHECK,
    TASK_TYPE_PATROL,
    TASK_TYPE_RECTIFY,
    DraftRequest,
    InspectionPlanDraftService,
)


def _req(**over):
    base = dict(
        idempotency_key="plan-draft-test-1",
        source_run_id="weekly-RECTIFY-20260920",
        plan_type="WEEKLY_RECTIFY",
        title="9月第3周整改复查",
    )
    base.update(over)
    return DraftRequest(**base)


def test_blueprint_weekly_rectify_prioritizes_rectify() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req())
    tasks = result.draft_content_json["tasks"]
    assert [t["task_type"] for t in tasks] == [TASK_TYPE_RECTIFY, TASK_TYPE_PATROL, TASK_TYPE_CHECK]
    assert tasks[0]["priority"] == "high"
    assert result.status == "DRAFT"
    assert result.model_version == DRAFT_MODEL_VERSION


def test_blueprint_weekly_patrol_prioritizes_patrol() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req(plan_type="WEEKLY_PATROL", idempotency_key="k2"))
    tasks = result.draft_content_json["tasks"]
    assert tasks[0]["task_type"] == TASK_TYPE_PATROL


def test_blueprint_weekly_check_prioritizes_check() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req(plan_type="WEEKLY_CHECK", idempotency_key="k3"))
    tasks = result.draft_content_json["tasks"]
    assert tasks[0]["task_type"] == TASK_TYPE_CHECK


def test_blueprint_unknown_plan_type_falls_back_generic() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req(plan_type="MONTHLY_AUDIT", idempotency_key="k4"))
    tasks = result.draft_content_json["tasks"]
    assert {t["task_type"] for t in tasks} == {
        TASK_TYPE_RECTIFY, TASK_TYPE_PATROL, TASK_TYPE_CHECK,
    }
    assert all(t["priority"] == "normal" for t in tasks)


def test_context_includes_factory_area_risk_basis() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req(
        factory="F1", area="A1", risk_basis="周报指向重复发生",
    ))
    ctx = result.draft_content_json["context"]
    assert ctx == {"factory": "F1", "area": "A1", "risk_basis": "周报指向重复发生"}
    assert "周报指向重复发生" in result.draft_content_json["tasks"][0]["rationale"]


def test_idempotency_same_key_returns_same_draft_id_and_status_replayed() -> None:
    svc = InspectionPlanDraftService()
    first = svc.draft(_req())
    second = svc.draft(_req())
    assert first.draft_id == second.draft_id
    assert first.status == "DRAFT"
    assert second.status == "REPLAYED"
    assert first.draft_content_json == second.draft_content_json


def test_different_keys_yield_different_draft_ids() -> None:
    svc = InspectionPlanDraftService()
    a = svc.draft(_req(idempotency_key="k-A"))
    b = svc.draft(_req(idempotency_key="k-B"))
    assert a.draft_id != b.draft_id


def test_schema_version_and_model_version_present() -> None:
    svc = InspectionPlanDraftService()
    result = svc.draft(_req())
    assert result.draft_content_json["schema_version"] == "plan-draft.v1"
    assert result.model_version.startswith("plan-draft/")