from __future__ import annotations

import asyncio

import pytest

from app.harness.kernel import Worker
from app.harness.kernel.domain.models import Conflict
from app.projects.cps.domain.models import (
    CreateInspection,
    DispatchReview,
    EvidenceInput,
    StopInspection,
    VersionedCommand,
)

from .conftest import (
    completed,
    create_started,
    execute_active,
    image_base64,
    proposal,
    report_output,
)
from .test_business import command, confirm_manual, create
from .test_workflow import confirm, dispatch


async def test_worker_projects_observer_only_manual_case_without_business_run(environment):
    service, runtime, engine = environment
    case = create(service)
    confirm_manual(service, case.id, "report", report_output())
    case = service.repository.get("tenant", case.id)
    engine.scripts["observation"].append(
        completed(
            memory_candidates=[
                {
                    "kind": "procedural",
                    "knowledge": "发布前保留确认记录",
                    "scope": "L1",
                    "condition": "报告发布",
                    "human_reason": None,
                    "outcome": "已归档",
                    "limitations": [],
                    "evaluation": "待后续观察",
                    "review_status": "pending",
                    "source_refs": ["history"],
                }
            ]
        )
    )
    service.archive("tenant", case.id, "admin", command(case, "archive"))
    assert all(run["purpose"] == "observer" for run in service.records.scan("tenant", "run"))
    worker = Worker(runtime, interval=0.01, observers=tuple(runtime.observers))
    worker.start()
    try:
        async with asyncio.timeout(5):
            while not list(service.records.scan("tenant", "memory")):
                await asyncio.sleep(0.01)
        assert [name for name, _ in engine.calls] == ["observation"]
        assert not service.memories("tenant", "L1")
    finally:
        await worker.stop()


async def test_after_evidence_is_bound_to_confirmed_issue_version(environment):
    service, _, _ = environment
    case = create(service)
    await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="before",
            kind="before",
            content_base64=image_base64(),
        ),
    )
    issue = {
        "issue_id": "one",
        "category": "机械",
        "description": "防护问题",
        "location": "工位1",
        "severity": "high",
        "suggested_action": "安装防护",
        "confidence": None,
        "limitations": [],
        "source_refs": ["inspection"],
    }
    confirm_manual(service, case.id, "issue_identification", completed(issues=[issue]))
    case = service.repository.get("tenant", case.id)
    await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="after",
            kind="after",
            issue_id="one",
            content_base64=image_base64(),
        ),
    )
    service._can_dispatch(service.repository.get("tenant", case.id), "rectification_judgement")
    confirm_manual(
        service,
        case.id,
        "issue_identification",
        completed(issues=[{**issue, "description": "修正后的问题"}]),
    )
    with pytest.raises(Conflict):
        service._can_dispatch(service.repository.get("tenant", case.id), "rectification_judgement")


async def test_round_budget_stops_without_looping(environment):
    service, _, _ = environment
    case = create(service)
    service.config.max_rounds = 1
    with service.records.transaction():
        case.rounds = 1
        service.repository.save("tenant", case)
    result = service.start(
        "tenant",
        case.id,
        "employee",
        VersionedCommand(
            expected_version=case.version,
            idempotency_key="start",
        ),
    )
    assert result.status == "needs_human" and result.active_job is None
    assert not list(service.records.scan("tenant", "run"))


async def test_expired_review_outbox_does_not_block_other_inspections(environment, monkeypatch):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    with monkeypatch.context() as context:
        context.setattr(service, "flush", lambda tenant: None)
        service.dispatch(
            "tenant",
            case.id,
            "admin",
            ["cps_admin"],
            DispatchReview(
                expected_version=case.version,
                idempotency_key="approve",
                action="approve",
                reason="确认",
            ),
        )
    job = service.records.get("tenant", "cps_job", case.active_job)
    approval = service.records.get("tenant", "approval", job["review"]["approval_id"])
    approval["expires"] = 0
    service.records.put("tenant", "approval", approval["id"], approval)
    other = service.create(
        "tenant",
        "employee",
        CreateInspection(
            goal="独立任务",
            line_info={"line_id": "L2", "area": "A", "supervisor_id": "S"},
            idempotency_key="another",
        ),
    )
    service.start("tenant", other.id, "employee", command(other, "start"))
    assert service.detail("tenant", other.id)["active_run"]["status"] == "queued"
    assert service.detail("tenant", case.id)["outbox_error"]["error_type"] == "Conflict"
    service.flush("tenant")
    assert (
        len(
            [
                event
                for event in service.events("tenant", case.id)
                if event["kind"] == "cps.delivery_error"
            ]
        )
        == 1
    )


async def test_all_cps_specialists_form_confirmed_report_and_published_plan(environment):
    service, runtime, engine = environment
    required = ["issues", "rectification", "history_analysis", "coverage", "report", "work_plan"]
    case = create(service, required)
    await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="before",
            kind="before",
            content_base64=image_base64(),
        ),
    )
    case = service.repository.get("tenant", case.id)
    service.start("tenant", case.id, "employee", command(case, "start"))

    async def specialist(agent, output=None):
        engine.scripts["main"].append(proposal(agent))
        await execute_active(service, runtime, case.id)
        dispatch(service, case.id)
        await execute_active(service, runtime, case.id)
        if output is not None:
            engine.scripts[agent].append(output)
        await execute_active(service, runtime, case.id)
        confirm(service, case.id)
        await execute_active(service, runtime, case.id)

    await specialist("issue_identification")
    case = service.repository.get("tenant", case.id)
    case = service.stop(
        "tenant",
        case.id,
        "admin",
        StopInspection(
            expected_version=case.version,
            idempotency_key="collect",
            action="return",
            reason="上传整改后照片",
        ),
    )
    after = await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="after",
            kind="after",
            issue_id="issue1",
            content_base64=image_base64(),
        ),
    )
    case = service.repository.get("tenant", case.id)
    service.start("tenant", case.id, "employee", command(case, "continue"))
    await specialist(
        "rectification_judgement",
        completed(
            rectification={
                "status": "pass",
                "checks": [
                    {
                        "issue_id": "issue1",
                        "criterion": "防护安装",
                        "verdict": "satisfied",
                        "source_refs": [f"evidence:{after['id']}"],
                    }
                ],
                "remaining_items": [],
                "next_action": "定期复查",
                "confidence": None,
            }
        ),
    )
    await specialist(
        "history_analysis",
        completed(
            history_analysis={
                "period": None,
                "scope": "tenant",
                "metrics": [
                    {
                        "name": "record_count",
                        "value": 0,
                        "unit": "count",
                        "denominator": None,
                        "source_refs": ["statistics"],
                    }
                ],
                "findings": ["尚无归档历史"],
                "limitations": ["不能评估长期效果"],
            }
        ),
    )
    await specialist(
        "coverage_analysis",
        completed(
            coverage={
                "baseline_ref": None,
                "gaps": [
                    {
                        "scope": "L1",
                        "kind": "insufficient_evidence",
                        "description": "缺少覆盖基线",
                        "priority": "medium",
                        "source_refs": ["coverage_baseline"],
                    }
                ],
                "recommendations": ["补充巡检清单"],
            }
        ),
    )
    await specialist("report", report_output())
    await specialist(
        "work_plan",
        completed(
            work_plan=[
                {
                    "task_id": "followup",
                    "objective": "补充巡检清单",
                    "scope": "L1",
                    "priority": "medium",
                    "owner_id": "employee",
                    "due_at": "2027-01-01T00:00:00+08:00",
                    "acceptance_criteria": ["清单经主管确认"],
                    "dependencies": [],
                    "draft": True,
                    "source_refs": ["confirmed:coverage"],
                }
            ]
        ),
    )
    await execute_active(service, runtime, case.id)
    dispatch(service, case.id)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    assert case.status == "needs_human" and not case.can_finish()
    service.archive("tenant", case.id, "admin", command(case, "archive"))
    case = service.repository.get("tenant", case.id)
    service.publish_plan("tenant", case.id, "admin", command(case, "publish"))
    case = service.repository.get("tenant", case.id)
    assert case.can_finish()
    engine.scripts["main"].append(proposal(None, "finish"))
    service.start("tenant", case.id, "employee", command(case, "finish"))
    await execute_active(service, runtime, case.id)
    dispatch(service, case.id)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    assert case.status == "completed" and case.completed_at is not None
    assert set(required) <= set(case.confirmations)
    assert service.statistics("tenant")["measures"]["安装防护罩"]["verified_pass"] == 1
    assert len(list(service.records.scan("tenant", "cps_task"))) == 1
