"""B6 视觉点检 Agent Java 回调客户端测试。

覆盖目标：
1. JavaRoomCheckCallbackClient round-trip：成功路径（MockTransport 200）
2. 重试行为：HTTP 5xx 触发 1+max_retries 次重试
3. 退避：退避 sleep 可关闭（backoff=()）→ 仅调用，无延迟
4. fingerprint URL 模板注入防御（拒绝含 / 或 {} 的 fingerprint）
5. payload 字段完整（fingerprint/overall/score/reasons 四字段）
6. FakeRoomCheckCallbackClient 记录 dispatches + 脚本消费
7. Failure logging：连续失败后 last_error 字段携带错误细节
8. judge_service → 异步 dispatch callback 闭环
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.projects.room_checks.domain.evidence_models import (
    JudgeVerdict,
    RoomCheckEvidence,
    RoomCheckVerdict,
)
from app.projects.room_checks.infrastructure.callback_client import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    CallbackOutcome,
    FakeRoomCheckCallbackClient,
    JavaRoomCheckCallbackClient,
)
from app.projects.room_checks.infrastructure.vision_client import (
    FakeVisionModelClient,
    VisionCallResult,
)


# -- helpers -----------------------------------------------------------------
def _verdict(
    fingerprint: str = "rc-fp-001",
    overall: JudgeVerdict = JudgeVerdict.PASS,
    score: int = 100,
    reasons: tuple[str, ...] = ("ok",),
) -> RoomCheckVerdict:
    return RoomCheckVerdict(
        fingerprint=fingerprint,
        overall=overall,
        score=score,
        reasons=reasons,
        judged_at=datetime.now(UTC).replace(tzinfo=None),
        model_name="qwen-vl-plus",
        raw_output={"x": 1},
    )


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


# -- JavaRoomCheckCallbackClient round-trip ----------------------------------
@pytest.mark.asyncio
async def test_java_callback_round_trip_success() -> None:
    """MockTransport 200 → CallbackOutcome(ok=True, attempts=1, last_error=None)。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    client = JavaRoomCheckCallbackClient(transport=httpx.MockTransport(handler))
    outcome = await client.dispatch(_verdict())

    assert outcome.ok is True
    assert outcome.attempts == 1
    assert outcome.last_error is None
    assert outcome.fingerprint == "rc-fp-001"

    # 验证 URL + Body
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/api/cps/room-checks/rc-fp-001/callback"
    assert req.method == "POST"
    body = json.loads(req.content)
    assert body == {
        "fingerprint": "rc-fp-001",
        "overall": "PASS",
        "score": 100,
        "reasons": ["ok"],
    }


@pytest.mark.asyncio
async def test_java_callback_retries_on_http_500() -> None:
    """HTTP 500 连续两次 → 第 3 次成功（默认 max_retries=2）。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(500, text="server error")
        return httpx.Response(200, json={"ok": True})

    # 关闭退避以加速单测
    client = JavaRoomCheckCallbackClient(
        transport=httpx.MockTransport(handler),
        backoff=(),
    )
    outcome = await client.dispatch(_verdict())

    assert outcome.ok is True
    assert outcome.attempts == 3
    assert attempts == 3


@pytest.mark.asyncio
async def test_java_callback_returns_failure_when_all_attempts_fail() -> None:
    """3 次都失败 → CallbackOutcome(ok=False, attempts=3, last_error != None)。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = JavaRoomCheckCallbackClient(
        transport=httpx.MockTransport(handler),
        backoff=(),
    )
    outcome = await client.dispatch(_verdict())

    assert outcome.ok is False
    assert outcome.attempts == 1 + DEFAULT_MAX_RETRIES
    assert outcome.last_error is not None
    assert "HTTP 502" in outcome.last_error


@pytest.mark.asyncio
async def test_java_callback_retries_on_transport_error() -> None:
    """网络层 ConnectError → 重试。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise httpx.ConnectError("simulated", request=request)
        return httpx.Response(200, json={"ok": True})

    client = JavaRoomCheckCallbackClient(
        transport=httpx.MockTransport(handler),
        backoff=(),
    )
    outcome = await client.dispatch(_verdict())

    assert outcome.ok is True
    assert outcome.attempts == 2


@pytest.mark.asyncio
async def test_java_callback_rejects_unsafe_fingerprint() -> None:
    """fingerprint 含 '/' 或 '{}' → ValueError（防 URL 模板注入）。"""
    client = JavaRoomCheckCallbackClient()
    for bad in ("rc/../foo", "rc{x}", "rc}{"):
        with pytest.raises(ValueError):
            await client.dispatch(_verdict(fingerprint=bad))


@pytest.mark.asyncio
async def test_java_callback_uses_default_timeout() -> None:
    """默认 timeout = 30s（与任务书一致）。"""
    assert DEFAULT_TIMEOUT_SECONDS == 30.0


@pytest.mark.asyncio
async def test_java_callback_backoff_default_matches_initial_review() -> None:
    """默认退避 (1.0, 3.0) 与 initial_review 回调同模式。"""
    assert DEFAULT_BACKOFF == (1.0, 3.0)


# -- FakeRoomCheckCallbackClient ---------------------------------------------
@pytest.mark.asyncio
async def test_fake_callback_records_dispatches() -> None:
    """每次 dispatch 把 verdict 计入 dispatches（便于断言回调闭环）。"""
    fake = FakeRoomCheckCallbackClient()
    v = _verdict()
    await fake.dispatch(v)
    await fake.dispatch(v)
    assert len(fake.dispatches) == 2
    assert all(d.fingerprint == "rc-fp-001" for d in fake.dispatches)


@pytest.mark.asyncio
async def test_fake_callback_consumes_scripts() -> None:
    """脚本按顺序消费；脚本耗尽时返回 ok=True（兼容默认行为）。"""
    scripts = [
        CallbackOutcome(
            ok=False, attempts=1, last_error="simulated", fingerprint="rc-fp-001"
        ),
        CallbackOutcome(
            ok=True, attempts=2, last_error=None, fingerprint="rc-fp-001"
        ),
    ]
    fake = FakeRoomCheckCallbackClient(scripts=scripts)
    first = await fake.dispatch(_verdict())
    second = await fake.dispatch(_verdict())
    third = await fake.dispatch(_verdict())  # 脚本耗尽
    assert first.ok is False
    assert second.ok is True
    assert third.ok is True  # default fallback


@pytest.mark.asyncio
async def test_fake_callback_propagates_exception_script() -> None:
    """脚本为 Exception 实例时重新抛出（便于测试 service 错误处理）。"""
    fake = FakeRoomCheckCallbackClient(scripts=[RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        await fake.dispatch(_verdict())


# -- service → callback 闭环 -------------------------------------------------
@pytest.mark.asyncio
async def test_judge_service_invokes_callback_after_verdict() -> None:
    """judge 完成后异步 dispatch callback（fire-and-forget 模式）。"""
    from app.projects.room_checks.application.judge_service import (
        RoomCheckB6JudgeService,
    )
    from app.projects.room_checks.domain.evidence_models import RoomType

    fake_cb = FakeRoomCheckCallbackClient()
    service = RoomCheckB6JudgeService(
        vision_client=FakeVisionModelClient(results=_scripted_pass_results()),
        callback_client=fake_cb,
    )

    evidence = RoomCheckEvidence(
        fingerprint="rc-fp-001",
        room_type=RoomType.PRIMARY,
        check_item_id="ci-001",
        photo_object_key="room/101/ground-1.jpg",
        photo_url=None,
        expected_match_type="地面",
        expected_keywords=("无杂物",),
        submitted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    verdict = await service.judge(evidence)
    # 排空 pending callbacks
    pending = service.pending_callbacks
    for awaitable in pending:
        await awaitable

    assert verdict.overall in {JudgeVerdict.PASS, JudgeVerdict.PARTIAL}
    assert len(fake_cb.dispatches) >= 1
    assert fake_cb.dispatches[0].fingerprint == "rc-fp-001"


@pytest.mark.asyncio
async def test_judge_service_dispatch_pending_callbacks_drains() -> None:
    """drain_pending() 返回并清空待处理 callback（与 initial_review 同模式）。"""
    from app.projects.room_checks.application.judge_service import (
        RoomCheckB6JudgeService,
    )
    from app.projects.room_checks.domain.evidence_models import RoomType

    fake_cb = FakeRoomCheckCallbackClient()
    service = RoomCheckB6JudgeService(
        vision_client=FakeVisionModelClient(results=_scripted_pass_results()),
        callback_client=fake_cb,
    )
    evidence = RoomCheckEvidence(
        fingerprint="rc-fp-002",
        room_type=RoomType.PRIMARY,
        check_item_id="ci-002",
        photo_object_key="room/101/ground-2.jpg",
        photo_url=None,
        expected_match_type="地面",
        expected_keywords=("无杂物",),
        submitted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    await service.judge(evidence)
    drained = service.drain_pending()
    assert isinstance(drained, list)
    for awaitable in drained:
        await awaitable
    # 排空后为空
    assert service.pending_callbacks == []
    assert len(fake_cb.dispatches) >= 1