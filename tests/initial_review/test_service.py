"""InitialReviewService 行为测试：幂等/重放/超时/扫描/回调/overall 计算。"""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta

import pytest
from sqlalchemy import func, select

from app.models.agent_initial_review import AgentInitialReviewExec
from app.projects.initial_review.application.service import (
    CallbackPushBudgetExhausted,
    InitialReviewService,
    ReplayConflict,
    TaskNotTerminal,
)
from app.projects.initial_review.infrastructure.callback import JavaCallbackClient
from tests.initial_review.conftest import (
    VALID_REASON,
    FakeCallbackClient,
    drain,
    insert_exec_row,
    make_request,
    make_service,
    make_session_factory,
    make_settings,
    naive_utc_now,
)


async def test_submit_completes_and_dispatches_callback() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    resp = await service.submit(make_request())

    assert resp["review_task_ref"] == "cps-rectify-ISS-001-v1"
    assert resp["status"] == "COMPLETED"
    assert resp["replayed"] is False
    # 文本规则先行（毫秒级同步）已落结果
    async with sf() as session:
        row = (
            await session.execute(select(AgentInitialReviewExec))
        ).scalar_one()
    assert row.text_checks["reason"]["length"] == len(VALID_REASON)
    assert row.text_checks["reason"]["valid"] is True
    # 波次 2：A6 确定性雷同恒实现；A7/A8 模型检查未装配（单测无 LLM）→ SKIPPED 不伪造
    assert row.model_checks["measure_similarity"]["implementation_status"] == "IMPLEMENTED"
    assert row.model_checks["measure_similarity"]["verdict"] == "PASS"
    assert row.model_checks["text_validity"]["implementation_status"] == "SKIPPED"
    assert row.model_checks["image_compare"]["implementation_status"] == "SKIPPED"
    # 文本规则全过 + A6 过，但 A7/A8 未出具结论 → PARTIAL（部分完成，不伪造通过）
    assert row.overall == "PARTIAL"
    assert row.model_version == "text-rules/d-22@1+measure-similarity/det@1"
    # deadline = started + 480s
    assert row.deadline_at - row.started_at == timedelta(seconds=480)

    await drain(pending)
    assert len(callback.payloads) == 1
    payload = callback.payloads[0]
    assert payload["idempotency_key"] == "initial-review-result-cps-rectify-ISS-001-v1"
    assert payload["status"] == "COMPLETED"
    assert payload["overall"] == "PARTIAL"
    assert payload["is_late"] is False
    types = {(i["check_type"], i["field_name"]): i for i in payload["items"]}
    assert types[("TEXT_LENGTH", "reason")]["verdict"] == "PASS"
    assert types[("TEXT_LENGTH", "reason")]["text_length"] == len(VALID_REASON)
    assert types[("PUNCTUATION_RATIO", "reason")]["punctuation_count"] == 0
    skipped = [i for i in payload["items"] if i["verdict"] == "SKIPPED"]
    assert {i["implementation_status"] for i in skipped} == {"SKIPPED"}
    assert len(skipped) == 4  # 3×TEXT_VALIDITY + IMAGE_COMPARE（A6 已实现为 PASS）
    # 回调投递状态落库
    async with sf() as session:
        row = (
            await session.execute(select(AgentInitialReviewExec))
        ).scalar_one()
    assert row.callback_status == "SENT"


async def test_submit_idempotent_replay_same_ref_single_row() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    first = await service.submit(make_request())
    await drain(pending)
    second = await service.submit(make_request())
    await drain(pending)

    assert first["review_task_ref"] == second["review_task_ref"]
    assert second["replayed"] is True
    assert second["status"] == "COMPLETED"
    # 唯一索引兜底：仍只有一行
    async with sf() as session:
        count = (
            await session.execute(select(func.count()).select_from(AgentInitialReviewExec))
        ).scalar_one()
    assert count == 1
    # 波次 8（J 线转交 #3）：终态行重放 → 补发一次回调（同幂等键，Java 去重）
    assert len(callback.payloads) == 2
    assert callback.payloads[0]["idempotency_key"] == callback.payloads[1]["idempotency_key"]


async def test_replay_terminal_row_re_sends_callback_without_reexecute() -> None:
    """终态行重放不重跑检查（check_runner 只执行一次），只补发回调。"""
    sf = await make_session_factory()
    runs: list[str] = []

    async def counting_runner(row, request):  # type: ignore[no-untyped-def]
        runs.append(row.task_id)
        return {}, {}, "PARTIAL"

    service, callback, pending = make_service(sf, check_runner=counting_runner)

    await service.submit(make_request())
    await drain(pending)
    resp = await service.submit(make_request())
    await drain(pending)

    assert runs == ["cps-rectify-ISS-001-v1"]  # 未重跑
    assert resp["replayed"] is True
    assert resp["status"] == "COMPLETED"
    assert len(callback.payloads) == 2


async def test_replay_running_row_executes_normally_single_callback() -> None:
    """行仍在跑（进程崩溃遗留 RUNNING）→ 维持现状：走执行链，终态时恰好一次回调。"""
    from tests.initial_review.conftest import insert_exec_row

    sf = await make_session_factory()
    service, callback, pending = make_service(sf)
    request = make_request(issue_id="ISS-002", submission_id="SUB-2026-002")
    task_id = await insert_exec_row(
        sf, issue_id="ISS-002", status="RUNNING", fingerprint=service._fingerprint(request)
    )

    resp = await service.submit(request)
    await drain(pending)

    assert resp["review_task_ref"] == task_id
    assert resp["status"] == "COMPLETED"  # 执行链接手遗留 RUNNING 行
    assert len(callback.payloads) == 1  # 不额外补发（未终态前维持现状）


async def test_submit_same_key_different_params_conflict() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    await service.submit(make_request())
    await drain(pending)
    with pytest.raises(ReplayConflict):
        await service.submit(make_request(reason="另一个整改原因，参数不同，应当冲突"))
    await drain(pending)
    assert len(callback.payloads) == 1


async def test_version_bump_creates_new_task() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    v1 = await service.submit(make_request())
    v2 = await service.submit(make_request(version_no=2))
    await drain(pending)

    assert v1["review_task_ref"] == "cps-rectify-ISS-001-v1"
    assert v2["review_task_ref"] == "cps-rectify-ISS-001-v2"
    assert len(callback.payloads) == 2


async def test_any_field_fail_makes_problem() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    await service.submit(make_request(short_term_measure="太短"))
    await drain(pending)

    status = await service.status("cps-rectify-ISS-001-v1")
    assert status is not None
    assert status["overall"] == "PROBLEM"
    assert status["text_checks"]["short_term"]["valid"] is False
    assert status["text_checks"]["reason"]["valid"] is True
    payload = callback.payloads[0]
    assert payload["overall"] == "PROBLEM"
    types = {(i["check_type"], i["field_name"]): i for i in payload["items"]}
    assert types[("TEXT_LENGTH", "short_term")]["verdict"] == "FAIL"


async def test_wall_clock_deadline_marks_failed_timeout() -> None:
    sf = await make_session_factory()

    async def slow_runner(row, request):
        await asyncio.sleep(0.2)
        return {}, {}, "PASS"

    service, callback, pending = make_service(
        sf, settings=make_settings(deadline_seconds=0.05), check_runner=slow_runner
    )

    resp = await service.submit(make_request())
    await drain(pending)

    assert resp["status"] == "FAILED"
    status = await service.status("cps-rectify-ISS-001-v1")
    assert status is not None
    assert status["error_code"] == "TIMEOUT"
    assert "超时" in (status["error"] or "")
    assert status["finished_at"] is not None
    # 超时也是终态 → 照常回调（Java 立即可接管）
    assert len(callback.payloads) == 1
    assert callback.payloads[0]["status"] == "FAILED"
    assert callback.payloads[0]["error_code"] == "TIMEOUT"


async def test_expired_running_row_swept_by_status_query() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    # 模拟进程崩溃遗留：手工插入 RUNNING 且 deadline 已过
    from datetime import datetime

    now = datetime.now(UTC).replace(tzinfo=None)
    async with sf() as session, session.begin():
        session.add(
            AgentInitialReviewExec(
                task_id="cps-rectify-GHOST-v9",
                issue_id="GHOST",
                version_no=9,
                status="RUNNING",
                started_at=now - timedelta(seconds=600),
                deadline_at=now - timedelta(seconds=120),
            )
        )

    status = await service.status("cps-rectify-GHOST-v9")
    await drain(pending)

    assert status is not None
    assert status["status"] == "FAILED"
    assert status["error_code"] == "TIMEOUT"
    assert "惰性扫描" in (status["error"] or "")
    # 崩溃遗留行被 sweep 不触发回调（Java 兜底轮询会发现 FAILED）
    assert len(callback.payloads) == 0


async def test_status_unknown_task_returns_none() -> None:
    sf = await make_session_factory()
    service, _, _ = make_service(sf)
    assert await service.status("cps-rectify-NOPE-v1") is None


async def test_callback_retried_then_recorded() -> None:
    sf = await make_session_factory()
    # 真实 JavaCallbackClient + MockTransport：前 2 次 500、第 3 次 200；
    # backoff 为空 → 不真实等待，验证重试循环与 attempts 如实上报。
    import httpx

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500 if len(calls) <= 2 else 200)

    client = JavaCallbackClient(
        make_settings(callback_backoff_seconds=()), transport=httpx.MockTransport(handler)
    )
    pending: list = []
    service = InitialReviewService(
        sf, client, make_settings(), clock=naive_utc_now,
        callback_dispatcher=lambda coro: pending.append(coro),
    )

    await service.submit(make_request())
    while pending:
        await pending.pop(0)

    assert calls == ["/api/callbacks/initial-review/result"] * 3
    async with sf() as session:
        row = (
            await session.execute(select(AgentInitialReviewExec))
        ).scalar_one()
    assert row.callback_status == "SENT"
    assert row.callback_attempts == 3
    assert row.callback_last_error is None


async def test_callback_all_retries_failed() -> None:
    sf = await make_session_factory()
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = JavaCallbackClient(
        make_settings(callback_backoff_seconds=()), transport=httpx.MockTransport(handler)
    )
    pending: list = []
    service = InitialReviewService(
        sf, client, make_settings(), clock=naive_utc_now,
        callback_dispatcher=lambda coro: pending.append(coro),
    )

    resp = await service.submit(make_request())
    while pending:
        await pending.pop(0)

    assert resp["status"] == "COMPLETED"  # 回调失败不影响任务终态
    async with sf() as session:
        row = (
            await session.execute(select(AgentInitialReviewExec))
        ).scalar_one()
    assert row.callback_status == "FAILED"
    assert row.callback_attempts == 3  # 1 + max_retries(2)
    assert "HTTP 503" in (row.callback_last_error or "")


async def test_task_ref_formula() -> None:
    assert InitialReviewService.task_ref("ISS-9", 3) == "cps-rectify-ISS-9-v3"


# --------------------------------------------- 波次 8（J 线转交 #4）：手动重推 ----


async def test_repush_callback_sends_and_accumulates_attempts() -> None:
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)
    await service.submit(make_request())
    await drain(pending)  # 初次投递 attempts=1

    result = await service.repush_callback("cps-rectify-ISS-001-v1")

    assert result is not None
    assert result["pushed"] is True
    assert result["callback_status"] == "SENT"
    assert result["callback_attempts"] == 2  # 累计口径：初次 1 + 重推 1
    assert len(callback.payloads) == 2
    assert callback.payloads[1]["idempotency_key"] == "initial-review-result-cps-rectify-ISS-001-v1"
    async with sf() as session:
        row = (
            await session.execute(
                select(AgentInitialReviewExec).where(
                    AgentInitialReviewExec.task_id == "cps-rectify-ISS-001-v1"
                )
            )
        ).scalar_one()
    assert row.callback_status == "SENT"
    assert row.callback_attempts == 2


async def test_repush_callback_failed_send_records_failure() -> None:
    """重推发送失败（Java 仍故障）：如实记 FAILED/last_error，attempts 照常累计。"""
    sf = await make_session_factory()
    service, callback, pending = make_service(sf, callback=FakeCallbackClient(failures=99))
    await service.submit(make_request())
    await drain(pending)  # 初次投递也失败（attempts=1, FAILED）

    result = await service.repush_callback("cps-rectify-ISS-001-v1")

    assert result is not None
    assert result["pushed"] is False
    assert result["callback_status"] == "FAILED"
    assert result["callback_attempts"] == 2
    assert "simulated failure" in (result["last_error"] or "")
    async with sf() as session:
        row = (
            await session.execute(select(AgentInitialReviewExec))
        ).scalar_one()
    assert row.callback_status == "FAILED"


async def test_repush_unknown_task_returns_none() -> None:
    sf = await make_session_factory()
    service, _, _ = make_service(sf)
    assert await service.repush_callback("cps-rectify-nope-v1") is None


async def test_repush_running_row_rejected() -> None:
    sf = await make_session_factory()
    service, callback, _ = make_service(sf)
    task_id = await insert_exec_row(sf, issue_id="ISS-003", status="RUNNING")

    with pytest.raises(TaskNotTerminal):
        await service.repush_callback(task_id)
    assert callback.payloads == []  # 未发送


async def test_repush_budget_exhausted_rejected() -> None:
    """累计尝试达上限（默认 20）→ 429 语义（配额防滥用），不再发送。"""
    sf = await make_session_factory()
    service, callback, _ = make_service(sf, settings=make_settings())
    task_id = await insert_exec_row(sf, issue_id="ISS-004", callback_attempts=20)

    with pytest.raises(CallbackPushBudgetExhausted) as exc_info:
        await service.repush_callback(task_id)
    assert exc_info.value.limit == 20
    assert callback.payloads == []
