from __future__ import annotations

import asyncio
import time

import pytest

from app.harness.kernel import Worker
from app.harness.kernel.domain.models import Conflict
from app.projects.cps.domain.contracts import CONTRACTS
from app.projects.cps.domain.models import (
    CreateInspection,
    EvidenceInput,
    ManualResult,
    MemoryReview,
    StopInspection,
    TaskUpdate,
    VersionedCommand,
)
from app.projects.cps.infrastructure.agents import initial_state, prompt_for

from .conftest import completed, create_started, execute_active, image_base64, report_output


def create(service, required=None):
    return service.create(
        "tenant",
        "employee",
        CreateInspection(
            goal="巡检",
            line_info={"line_id": "L1", "area": "A1", "supervisor_id": "S1"},
            required_outputs=required or [],
            idempotency_key="manual",
        ),
    )


def confirm_manual(service, case_id, agent, output):
    case = service.repository.get("tenant", case_id)
    return service.manual_result(
        "tenant",
        case_id,
        "admin",
        ManualResult(
            expected_version=case.version,
            idempotency_key=f"manual-{case.version}",
            agent=agent,
            output=output,
            reason="现场确认",
        ),
    )


def command(case, key):
    return VersionedCommand(expected_version=case.version, idempotency_key=key)


def test_report_archive_plan_publication_and_dependency_lifecycle(environment):
    service, _, _ = environment
    case = create(service, ["report", "work_plan"])
    confirm_manual(service, case.id, "report", report_output('<script>alert("x")</script>'))
    content, confirmed = service.report("tenant", case.id)
    assert confirmed and "<script>" not in content and "&lt;script&gt;" in content
    case = service.repository.get("tenant", case.id)
    body = command(case, "archive")
    first = service.archive("tenant", case.id, "admin", body)
    second = service.archive("tenant", case.id, "admin", body)
    assert first == second
    assert len(list(service.records.scan("tenant", "cps_archive"))) == 1
    task = {
        "task_id": "first",
        "objective": "整改复查",
        "scope": "L1",
        "priority": "high",
        "owner_id": "employee",
        "due_at": "2027-01-01T10:00:00+08:00",
        "acceptance_criteria": ["防护安装并复查"],
        "dependencies": [],
        "source_refs": ["inspection"],
        "draft": True,
    }
    confirm_manual(
        service,
        case.id,
        "work_plan",
        completed(work_plan=[task, {**task, "task_id": "second", "dependencies": ["first"]}]),
    )
    case = service.repository.get("tenant", case.id)
    body = command(case, "publish")
    plan = service.publish_plan("tenant", case.id, "admin", body)
    assert service.publish_plan("tenant", case.id, "admin", body) == plan
    assert service.repository.get("tenant", case.id).can_finish()
    tasks = [service.records.get("tenant", "cps_task", task_id) for task_id in plan["task_ids"]]
    first = next(task for task in tasks if not task["dependencies"])
    second = next(task for task in tasks if task["dependencies"])
    update = TaskUpdate(
        expected_version=1, idempotency_key="done", status="completed", note="检查完成"
    )
    with pytest.raises(Conflict):
        service.update_task("tenant", second["id"], "employee", update)
    service.update_task("tenant", first["id"], "employee", update)
    service.update_task("tenant", first["id"], "employee", update)
    assert service.update_task("tenant", second["id"], "employee", update)["status"] == "completed"


def test_memory_review_disable_enable_rollback_and_scope(environment):
    service, _, _ = environment
    from app.harness.kernel import Memory

    memory_id = Memory(service.records).propose(
        "tenant",
        "cps",
        {
            "scope": "L1",
            "knowledge": "证据不完整时退回采集",
        },
        source_run="source",
        key="memory",
    )
    assert service.memories("tenant", "L1") == []
    record = service.review_memory(
        "tenant",
        memory_id,
        "admin",
        MemoryReview(expected_version=1, action="accept", reason="证据充分"),
    )
    assert service.memories("tenant", "L1")
    assert not service.memories("other", "L1")
    assert not service.memories("tenant", "L2")
    record = service.review_memory(
        "tenant",
        memory_id,
        "admin",
        MemoryReview(expected_version=record["version"], action="disable", reason="暂停评估"),
    )
    assert not service.memories("tenant", "L1")
    record = service.review_memory(
        "tenant",
        memory_id,
        "admin",
        MemoryReview(
            expected_version=record["version"],
            action="rollback",
            rollback_version=2,
            reason="恢复已审版本",
        ),
    )
    assert record["version"] == 4 and record["state"] == "accepted"
    assert len(list(service.records.scan("tenant", "memory_history"))) == 3


async def test_observer_runs_readonly_and_proposes_only_reviewable_memories(environment):
    service, runtime, engine = environment
    case = create(service)
    confirm_manual(service, case.id, "report", report_output())
    case = service.repository.get("tenant", case.id)
    service.archive("tenant", case.id, "admin", command(case, "archive"))
    job = next(
        job for job in service.records.scan("tenant", "cps_job") if job["agent"] == "observation"
    )
    engine.scripts["observation"].append(
        completed(
            memory_candidates=[
                {
                    "kind": "procedural",
                    "knowledge": "保留人工确认版本",
                    "scope": "任意范围不可自行授权",
                    "condition": "确认报告时",
                    "human_reason": "现场复核",
                    "outcome": "已确认报告",
                    "limitations": ["还没有长期效果数据"],
                    "evaluation": "后续观察",
                    "review_status": "pending",
                    "source_refs": ["history"],
                }
            ]
        )
    )
    run = await runtime.execute("tenant", job["run_id"])
    service.advance("tenant", run["id"])
    assert run["status"] == "succeeded"
    candidates = list(service.records.scan("tenant", "memory"))
    assert len(candidates) == 1 and candidates[0]["state"] == "proposed"
    assert candidates[0]["content"]["scope"] == "L1"
    assert not service.memories("tenant", "L1")
    assert runtime.registry["cps_observation"].delegates == ()
    assert runtime.registry["cps_observation"].tools == ()


async def test_worker_reaches_human_gate_without_running_specialist(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    worker = Worker(runtime, interval=0.01, observers=tuple(runtime.observers))
    worker.start()
    try:
        async with asyncio.timeout(5):
            while not service.detail("tenant", case.id)["approvals"]:
                await asyncio.sleep(0.01)
        assert [name for name, _ in engine.calls] == ["main"]
    finally:
        await worker.stop()


async def test_return_stops_pending_run_and_evidence_update_invalidates_old_results(environment):
    service, runtime, engine = environment
    case = await create_started(service, engine)
    await execute_active(service, runtime, case.id)
    case = service.repository.get("tenant", case.id)
    case = service.stop(
        "tenant",
        case.id,
        "admin",
        StopInspection(
            expected_version=case.version,
            idempotency_key="return",
            action="return",
            reason="补拍",
        ),
    )
    assert case.status == "needs_input" and case.active_job is None
    confirm_manual(service, case.id, "report", report_output())
    case = service.repository.get("tenant", case.id)
    await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="new-image",
            kind="before",
            content_base64=image_base64(),
        ),
    )
    assert not service.repository.get("tenant", case.id).confirmations


@pytest.mark.parametrize("name", list(CONTRACTS))
def test_migrated_prompts_have_boundaries_and_contracts(name):
    prompt = prompt_for(name)
    assert len(prompt) > 2000
    assert "CPS 全局行为约束" in prompt and "输出 Schema" in prompt
    assert "人工" in prompt and "来源" in prompt


def test_multimodal_state_carries_actual_bytes_and_evidence_id():
    state = initial_state(
        {
            "goal": "inspect",
            "image_content": [
                {
                    "source_ref": "evidence:one",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        },
        None,
    )
    content = state["messages"][0]["content"]
    assert content[1]["text"] == "证据来源：evidence:one"
    assert content[2]["image_url"]["url"].startswith("data:image/png")


def test_statistics_ignore_drafts_and_deduplicate_archived_revisions(environment):
    service, _, _ = environment
    now = time.time()
    record = {
        "inspection_id": "old",
        "line_info": {"line_id": "L1", "area": "A1", "supervisor_id": "S1"},
        "results": {
            "issues": [{"category": "机械", "location": "P1", "suggested_action": "加装防护"}]
        },
        "archived_at": now - 10,
    }
    service.records.put("tenant", "cps_archive", "v1", {**record, "id": "v1"})
    service.records.put(
        "tenant", "cps_archive", "v2", {**record, "id": "v2", "archived_at": now - 5}
    )
    service.records.put("other", "cps_archive", "v3", {**record, "id": "v3"})
    stats = service.statistics("tenant")
    assert stats["record_count"] == 1 and stats["issue_count"] == 1
    assert stats["supervisor_counts"] == {"S1": 1}
    assert stats["recurrence_rate"] == 0


def test_output_rejects_fabricated_sources_and_statistics(environment):
    service, _, _ = environment
    case = create(service)
    snapshot = service._snapshot("tenant", case, "")
    output = report_output()
    output["report"]["sections"][0]["source_refs"] = ["invented"]
    with pytest.raises(ValueError, match="source"):
        service.validate_result("report", output, snapshot)
    history = completed(
        history_analysis={
            "period": None,
            "scope": "tenant",
            "findings": [],
            "limitations": [],
            "metrics": [
                {
                    "name": "issue_count",
                    "value": 999,
                    "unit": "issues",
                    "denominator": None,
                    "source_refs": ["statistics"],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="statistics"):
        service.validate_result("history_analysis", history, snapshot)
