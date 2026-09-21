from __future__ import annotations

import pytest

from app.harness.kernel.domain.models import Conflict, Forbidden
from app.projects.cps.domain.models import DispatchReview, ResultReview, VersionedCommand

from .conftest import create_started, execute_active, proposal, report_output


def dispatch(service, case_id, **kwargs):
    case = service.repository.get("tenant", case_id)
    return service.dispatch(
        "tenant",
        case_id,
        "supervisor",
        ["cps_supervisor"],
        DispatchReview(
            expected_version=case.version,
            idempotency_key=f"dispatch-{case.version}",
            action="approve",
            reason="确认下一步",
            **kwargs,
        ),
    )


def confirm(service, case_id, **kwargs):
    case = service.repository.get("tenant", case_id)
    return service.review_result(
        "tenant",
        case_id,
        "admin",
        ["cps_admin"],
        ResultReview(
            expected_version=case.version,
            idempotency_key=f"review-{case.version}",
            reason="现场人员核对确认",
            **kwargs,
        ),
    )


async def test_dynamic_dispatch_and_separate_result_confirmation(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    assert (await execute_active(service, runtime, case.id))["status"] == "waiting"
    assert [name for name, _ in engine.calls] == ["main"]
    dispatch(service, case.id)
    await execute_active(service, runtime, case.id)
    assert [name for name, _ in engine.calls] == ["main"]
    assert (await execute_active(service, runtime, case.id))["status"] == "waiting"
    assert "issues" not in service.repository.get("tenant", case.id).results
    assert engine.calls[-1][1]["image_content"][0]["data_url"].startswith("data:image/png;base64,")
    engine.scripts["main"].append(proposal(None, "finish"))
    confirm(service, case.id)
    await execute_active(service, runtime, case.id)
    assert "issues" in service.repository.get("tenant", case.id).confirmations
    await execute_active(service, runtime, case.id)
    dispatch(service, case.id)
    await execute_active(service, runtime, case.id)
    assert service.repository.get("tenant", case.id).status == "completed"
    assert [name for name, _ in engine.calls] == ["main", "issue_identification", "main"]
    candidates = list(service.records.scan("tenant", "memory"))
    assert any(record["content"].get("actual_output", {}).get("issues") for record in candidates)
    assert service.memories("tenant", "L1") == []


async def test_human_can_redirect_and_memory_retains_original_and_effective(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine, required=["report"])
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    body = DispatchReview(
        expected_version=case.version,
        idempotency_key="redirect",
        action="modify",
        selected_agent="report",
        reason="先汇总已有信息",
        instruction="突出数据缺口",
    )
    service.dispatch("tenant", case.id, "admin", ["cps_admin"], body)
    service.dispatch("tenant", case.id, "admin", ["cps_admin"], body)
    await execute_active(service, runtime, case.id)
    engine.scripts["report"].append(report_output())
    await execute_active(service, runtime, case.id)
    assert [name for name, _ in engine.calls] == ["main", "report"]
    assert engine.calls[-1][1]["instruction"] == "突出数据缺口"
    confirm(service, case.id, edited=report_output("修改后的标题"))
    await execute_active(service, runtime, case.id)
    memories = list(service.records.scan("tenant", "memory"))
    record = next(
        record for record in memories if record["content"].get("actual_output", {}).get("report")
    )
    assert record["content"]["decision"]["original"]["agent_name"] == "issue_identification"
    assert record["content"]["decision"]["selected_agent"] == "report"
    assert record["content"]["actual_output"]["report"]["title"] == "修改后的标题"


@pytest.mark.parametrize("action,status", [("return", "needs_input"), ("manual", "needs_human")])
async def test_return_and_manual_do_not_execute_specialist(environment, action, status):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    service.dispatch(
        "tenant",
        case.id,
        "admin",
        ["cps_admin"],
        DispatchReview(
            expected_version=case.version,
            idempotency_key="decision",
            action=action,
            reason="需要人工补充",
        ),
    )
    await execute_active(service, runtime, case.id)
    assert service.repository.get("tenant", case.id).status == status
    assert [name for name, _ in engine.calls] == ["main"]


async def test_skip_reconsults_main_and_does_not_execute_suggested_agent(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    service.dispatch(
        "tenant",
        case.id,
        "admin",
        ["cps_admin"],
        DispatchReview(
            expected_version=case.version,
            idempotency_key="skip",
            action="skip",
            reason="重新评估",
        ),
    )
    await execute_active(service, runtime, case.id)
    await execute_active(service, runtime, case.id)
    assert [name for name, _ in engine.calls] == ["main", "main"]


async def test_result_rejection_stops_and_does_not_create_business_facts(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    dispatch(service, case.id)
    await execute_active(service, runtime, case.id)
    await execute_active(service, runtime, case.id)
    confirm(service, case.id, accept=False)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    assert case.status == "needs_human"
    assert not case.results


async def test_unapproved_archive_and_early_finish_are_blocked(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine, required=["report", "work_plan"])
    with pytest.raises(Conflict):
        service.archive(
            "tenant",
            case.id,
            "admin",
            VersionedCommand(expected_version=case.version, idempotency_key="archive"),
        )
    engine.scripts["main"].clear()
    engine.scripts["main"].append(proposal(None, "finish"))
    await execute_active(service, runtime, case.id)
    with pytest.raises(Conflict):
        dispatch(service, case.id)


async def test_generic_submission_cannot_bypass_business_dispatch(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    legitimate = service.records.get("tenant", "cps_job", case.active_job)
    run = runtime.submit(
        "tenant",
        case.id,
        "cps_issue_identification",
        {
            "inspection_id": case.id,
            "job_id": legitimate["id"],
        },
        idempotency_key="bypass",
    )
    result = await runtime.execute("tenant", run["id"])
    assert result["status"] == "failed"
    assert not engine.calls


async def test_roles_stale_reviews_and_agent_allowlist(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    with pytest.raises(ValueError):
        DispatchReview(
            expected_version=case.version,
            idempotency_key="bad",
            action="modify",
            selected_agent="observation",
            reason="bad",
        )
    with pytest.raises(Forbidden):
        service.dispatch(
            "tenant",
            case.id,
            "employee",
            ["cps_employee"],
            DispatchReview(
                expected_version=case.version,
                idempotency_key="bad",
                action="approve",
                reason="bad",
            ),
        )
    dispatch(service, case.id)
    with pytest.raises(Conflict):
        service.dispatch(
            "tenant",
            case.id,
            "admin",
            ["cps_admin"],
            DispatchReview(
                expected_version=case.version,
                idempotency_key="old",
                action="approve",
                reason="old",
            ),
        )
