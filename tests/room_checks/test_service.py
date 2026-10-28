"""B6 判定链服务测试 — Fake 模型客户端 + MockTransport，不触网。

覆盖：两阶段判定链（类型不匹配短路 / JUDGED PASS|FAIL / UNJUDGEABLE）、
SKIPPED 不伪造（禁用 / 未提供照片 / provider 未配置）、技术失败不缓存
（ModelCallError / URL 拉取故障 / 超时）、幂等重放。
照片引用只有 URL 一种形态：按 URL 取字节 → data URL 送视觉模型。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.projects.cps.initial_review.infrastructure.config import (
    InitialReviewSettings,
    MinIOSettings,
    ModelCheckSettings,
)
from app.projects.cps.initial_review.infrastructure.model_client import (
    FakeModelCheckClient,
    ModelCallError,
)
from app.projects.cps.room_checks.application.service import (
    JudgeTechnicalError,
    RoomCheckJudgeService,
    derive_idempotency_key,
)
from app.projects.cps.room_checks.domain.models import (
    STAGE_CONTENT_JUDGE,
    STAGE_TYPE_MATCH,
    STATUS_JUDGED,
    STATUS_SKIPPED,
    STATUS_TYPE_MISMATCH,
    STATUS_UNJUDGEABLE,
    ContentJudgeSchema,
    JudgeRequest,
    TypeMatchSchema,
)

PHOTO_URL = "http://java.local/files/a.jpg"


def make_settings(
    *,
    enabled: bool = True,
    minio_ok: bool = True,
    budget: float = 90.0,
    max_photos: int = 4,
    attachment_timeout: float = 15.0,
    attachment_max_bytes: int = 10_000_000,
) -> InitialReviewSettings:
    return InitialReviewSettings(
        deadline_seconds=480.0,
        java_callback_base="http://127.0.0.1:8080",
        java_callback_path="/api/callbacks/initial-review/result",
        callback_timeout_seconds=10.0,
        callback_max_retries=2,
        callback_backoff_seconds=(30.0, 60.0),
        model_check=ModelCheckSettings(
            enabled,
            "qwen",
            "qwen-plus",
            "Qwen2.5-VL-7B-Instruct",
            0.1,
            budget,
            max_photos,
            attachment_timeout,
            attachment_max_bytes,
        ),
        minio=MinIOSettings(
            endpoint="http://127.0.0.1:9000" if minio_ok else "",
            access_key="minioadmin" if minio_ok else "",
            secret_key="minioadmin" if minio_ok else "",
            bucket="cps-attachments" if minio_ok else "",
        ),
    )


class UrlRecorder:
    """记录实际被拉取的 URL（证明只走 URL 一条路径）。"""

    def __init__(self) -> None:
        self.fetched: list[str] = []


def make_url_transport(
    *,
    status: int = 200,
    content: bytes = b"\xff\xd8\xff\xe0fake-jpeg",
    content_type: str | None = "image/jpeg",
    exc: Exception | None = None,
    recorder: UrlRecorder | None = None,
) -> httpx.MockTransport:
    """httpx.MockTransport：模拟 Java 侧文件地址（不触网）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.fetched.append(str(request.url))
        if exc is not None:
            raise exc
        headers = {"content-type": content_type} if content_type else {}
        return httpx.Response(status, headers=headers, content=content)

    return httpx.MockTransport(handler)


def make_req(**overrides) -> JudgeRequest:
    base = dict(
        idempotency_key="room-judge-sub-1-item-9-1",
        submission_id="sub-1",
        item_id="item-9",
        attempt=1,
        item_content="地面无杂物、无积水",
        item_type="地面",
        photo_urls=(PHOTO_URL,),
        deduction=10.0,
        config_version="v3",
        room_name="1号楼配电间",
    )
    base.update(overrides)
    return JudgeRequest(**base)


def make_service(
    *,
    results: list | None = None,
    unavailable: str | None = None,
    vision_enabled: bool | None = None,
    url_transport: httpx.AsyncBaseTransport | None = None,
    recorder: UrlRecorder | None = None,
    **settings_kw,
) -> tuple[RoomCheckJudgeService, FakeModelCheckClient, UrlRecorder]:
    """构造被测服务。

    ``vision_enabled=None``（默认）时跟随注入 settings 的 model_check.enabled
    （复刻生产回退链的旧行为，保持既有用例语义）；显式传入则覆盖，
    用于验证波次 7 开关独立性。
    """
    client = FakeModelCheckClient(results=results, unavailable=unavailable)
    settings = make_settings(**settings_kw)
    recorder = recorder or UrlRecorder()
    transport = url_transport or make_url_transport(recorder=recorder)
    svc = RoomCheckJudgeService(
        model_client=client,
        settings=settings,
        vision_enabled=(
            vision_enabled if vision_enabled is not None else settings.model_check.enabled
        ),
        url_transport=transport,
    )
    return svc, client, recorder


# --- 判定链 -------------------------------------------------------------------


async def test_type_mismatch_short_circuits_and_replays() -> None:
    svc, client, _ = make_service(results=[
        TypeMatchSchema(type_match=False, photo_subject="桌面", reason="第1张拍的是桌面"),
    ])
    result = await svc.judge(make_req())
    assert result.status == STATUS_TYPE_MISMATCH
    assert result.verdict is None
    assert "重拍" in result.reason
    assert result.stage_trace[STAGE_TYPE_MATCH]["status"] == "TYPE_MISMATCH"
    assert result.stage_trace[STAGE_CONTENT_JUDGE]["status"] == "NOT_REACHED"
    assert len(client.calls) == 1  # 未进入内容判定（AC-08）
    # 幂等重放：不再触模型
    replay = await svc.judge(make_req())
    assert replay.status == STATUS_TYPE_MISMATCH
    assert replay.replayed is True
    assert len(client.calls) == 1


async def test_judged_pass_full_chain_and_evidence() -> None:
    svc, client, recorder = make_service(results=[
        TypeMatchSchema(type_match=True, photo_subject="地面", reason="主体为地面"),
        ContentJudgeSchema(
            verdict="PASS", reason="地面整洁", evidence="地面无杂物、无积水痕迹", confidence=0.92
        ),
    ])
    result = await svc.judge(make_req())
    assert result.status == STATUS_JUDGED
    assert result.verdict == "PASS"
    assert result.evidence == "地面无杂物、无积水痕迹"
    assert result.stage_trace[STAGE_TYPE_MATCH]["status"] == "PASS"
    assert result.stage_trace[STAGE_CONTENT_JUDGE]["confidence"] == 0.92
    assert result.item_snapshot == {
        "content": "地面无杂物、无积水",
        "type": "地面",
        "deduction": 10.0,
        "config_version": "v3",
    }
    assert result.model_version == "room-judge/qwen-vl@1"
    assert result.prompt_versions[STAGE_TYPE_MATCH] == "room-type-match/qwen-vl@1"
    # 照片按 URL 取出并转 data URL 送视觉模型
    assert recorder.fetched == [PHOTO_URL]
    assert client.calls[0]["images"][0].startswith("data:image/jpeg;base64,")


async def test_judged_fail_returns_fail_verdict() -> None:
    svc, _, _ = make_service(results=[
        TypeMatchSchema(type_match=True, photo_subject="地面", reason="ok"),
        ContentJudgeSchema(
            verdict="FAIL", reason="墙角堆放纸箱", evidence="右下角杂物堆", confidence=0.88
        ),
    ])
    result = await svc.judge(make_req())
    assert result.status == STATUS_JUDGED
    assert result.verdict == "FAIL"
    assert result.reason == "墙角堆放纸箱"


async def test_content_unjudgeable_requires_retake() -> None:
    svc, _, _ = make_service(results=[
        TypeMatchSchema(type_match=True, photo_subject="地面", reason="ok"),
        ContentJudgeSchema(
            verdict="UNJUDGEABLE", reason="照片模糊", evidence="对焦不清", confidence=0.3
        ),
    ])
    result = await svc.judge(make_req())
    assert result.status == STATUS_UNJUDGEABLE
    assert result.verdict is None
    assert "补拍" in result.reason


# --- SKIPPED 不伪造 -------------------------------------------------------------


async def test_skipped_when_model_check_disabled() -> None:
    svc, client, _ = make_service(results=[], enabled=False)
    result = await svc.judge(make_req())
    assert result.status == STATUS_SKIPPED
    assert "禁用" in result.reason
    assert result.verdict is None
    assert client.calls == []  # 完全不触模型
    replay = await svc.judge(make_req())
    assert replay.replayed is True and replay.status == STATUS_SKIPPED


async def test_skipped_when_no_photo_urls() -> None:
    """没有照片不再 422：直接 SKIPPED，不触模型、不触网络。"""
    svc, client, recorder = make_service(results=[])
    result = await svc.judge(make_req(photo_urls=()))
    assert result.status == STATUS_SKIPPED
    assert "未提供照片" in result.reason
    assert result.verdict is None
    assert client.calls == []
    assert recorder.fetched == []


async def test_skipped_when_provider_unavailable_at_call_time() -> None:
    # enabled=true 但 provider 未配置：DashScope 客户端调用时才抛 ModelUnavailableError
    svc, _, _ = make_service(results=[], unavailable="LLM provider 'qwen' 未配置")
    result = await svc.judge(make_req())
    assert result.status == STATUS_SKIPPED
    assert "未配置" in result.reason


# --- 技术失败 → JudgeTechnicalError（不缓存） ------------------------------------


async def test_model_call_error_raises_technical_not_cached() -> None:
    svc, client, _ = make_service(results=[ModelCallError("网络异常")])
    with pytest.raises(JudgeTechnicalError, match="类型匹配阶段失败"):
        await svc.judge(make_req())
    # 同键重试（Java 侧行为）：客户端恢复后可成功，证明失败未缓存
    client.results = [
        TypeMatchSchema(type_match=True, photo_subject="地面", reason="ok"),
        ContentJudgeSchema(verdict="PASS", reason="合格", evidence="干净", confidence=0.9),
    ]
    result = await svc.judge(make_req())
    assert result.status == STATUS_JUDGED and result.replayed is False


async def test_stage_timeout_raises_technical() -> None:
    class SlowClient:
        async def complete_vision_json(self, prompt, image_data_urls, schema):
            await asyncio.sleep(0.05)
            raise AssertionError("不应到达")

    svc = RoomCheckJudgeService(
        model_client=SlowClient(),
        settings=make_settings(budget=0.01),
        url_transport=make_url_transport(),
    )
    with pytest.raises(JudgeTechnicalError, match="类型匹配阶段失败"):
        await svc.judge(make_req())


def test_derive_idempotency_key_matches_contract() -> None:
    assert derive_idempotency_key("sub-1", "item-9", 2) == "room-judge-sub-1-item-9-2"


# --- 波次 7:视觉判定开关与 initial_review.model_check 解耦 -----------------------

_PASS_CHAIN = [
    TypeMatchSchema(type_match=True, photo_subject="地面", reason="主体为地面"),
    ContentJudgeSchema(
        verdict="PASS", reason="地面整洁", evidence="地面无杂物、无积水痕迹", confidence=0.9
    ),
]


async def test_vision_switch_independent_off_while_model_check_on() -> None:
    """C-04 独立关:model_check.enabled=true 但 vision.enabled=false → SKIPPED。"""
    svc, client, _ = make_service(
        results=list(_PASS_CHAIN), enabled=True, vision_enabled=False
    )
    result = await svc.judge(make_req())
    assert result.status == STATUS_SKIPPED
    assert "禁用" in result.reason
    assert client.calls == []  # 完全不触模型


async def test_vision_switch_independent_on_while_model_check_off() -> None:
    """C-04 独立开:model_check.enabled=false 但 vision.enabled=true → 正常判定。"""
    svc, client, _ = make_service(
        results=list(_PASS_CHAIN), enabled=False, vision_enabled=True
    )
    result = await svc.judge(make_req())
    assert result.status == STATUS_JUDGED
    assert client.calls != []


# --- URL 形态（C-04 photo_urls） -------------------------------------------------


async def test_url_mode_fetches_http_and_sends_data_url() -> None:
    svc, client, _ = make_service(
        results=list(_PASS_CHAIN), url_transport=make_url_transport()
    )
    result = await svc.judge(make_req(photo_urls=(PHOTO_URL,)))
    assert result.status == STATUS_JUDGED
    assert result.photo_urls == (PHOTO_URL,)
    assert client.calls[0]["images"][0].startswith("data:image/jpeg;base64,")


async def test_url_mode_guesses_mime_from_suffix_when_header_missing() -> None:
    svc, client, _ = make_service(
        results=list(_PASS_CHAIN),
        url_transport=make_url_transport(content_type=None, content=b"png-bytes"),
    )
    await svc.judge(make_req(photo_urls=("http://java.local/a.png?sign=1",)))
    assert client.calls[0]["images"][0].startswith("data:image/png;base64,")


async def test_url_mode_http_error_is_technical_failure_not_cached() -> None:
    svc, client, _ = make_service(
        results=[], url_transport=make_url_transport(status=404)
    )
    req = make_req(photo_urls=(PHOTO_URL,))
    with pytest.raises(JudgeTechnicalError, match="HTTP 404"):
        await svc.judge(req)
    assert client.calls == []
    # 技术失败不缓存：同键重试仍重新拉取（而非重放旧结果）
    with pytest.raises(JudgeTechnicalError, match="HTTP 404"):
        await svc.judge(req)


async def test_url_mode_connect_error_is_technical_failure() -> None:
    svc, _, _ = make_service(
        results=[],
        url_transport=make_url_transport(exc=httpx.ConnectError("connection refused")),
    )
    with pytest.raises(JudgeTechnicalError, match="照片 URL 拉取失败"):
        await svc.judge(make_req(photo_urls=(PHOTO_URL,)))


async def test_url_mode_oversize_content_is_technical_failure() -> None:
    svc, _, _ = make_service(
        results=[],
        url_transport=make_url_transport(content=b"x" * 64),
        attachment_max_bytes=16,
    )
    with pytest.raises(JudgeTechnicalError, match="超过上限"):
        await svc.judge(make_req(photo_urls=(PHOTO_URL,)))


async def test_url_mode_ignores_missing_minio_config() -> None:
    """URL 形态不依赖对象存储：MinIO 未配置也应正常判定（不得 SKIPPED）。"""
    svc, _, _ = make_service(
        results=list(_PASS_CHAIN), minio_ok=False, url_transport=make_url_transport()
    )
    result = await svc.judge(make_req(photo_urls=(PHOTO_URL,)))
    assert result.status == STATUS_JUDGED
