"""BUG-1 回归测试：RustFS TCP 级失败与意外异常不得 500 / 卡 RUNNING。

背景（波次 6 A10/C7 发现）：
- fetch_bytes 只包 HTTP≠200，httpx.ConnectError 裸抛穿透两层 except → HTTP 500；
- service.py except CheckRunnerError 为死代码，意外异常后行卡 RUNNING 被扫描误标 TIMEOUT。

修复口径：
1. fetch_bytes 捕获 httpx.TransportError → ModelCallError（与 HTTP≠200 同语义）→ DEGRADED 不伪造；
2. _execute 兜底 except Exception → FAILED+CHECK_ERROR + 正常回调，不再裸抛。
"""

from __future__ import annotations

import httpx
import pytest

from app.projects.initial_review.application.check_pipeline import CheckPipeline
from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    RustFSObjectFetcher,
    RustFSSettings,
)

from .conftest import (
    drain,
    make_request,
    make_service,
    make_session_factory,
    make_settings,
)
from .test_a10_boundaries import FakeModelCheckClient
from .test_check_pipeline import image_result, snapshot_of, valid_field


async def test_rustfs_connect_error_degrades_not_500() -> None:
    """RustFS TCP 连接失败（ConnectError）→ 与 503 同语义：DEGRADED + SKIPPED 不伪造。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    cfg = RustFSSettings(
        endpoint="http://rustfs.test",
        access_key="ak",
        secret_key="sk",
        bucket="cps-attachments",
    )
    fetcher = RustFSObjectFetcher(cfg, transport=httpx.MockTransport(handler))
    pipe = CheckPipeline(
        make_settings(),
        model_client=FakeModelCheckClient(
            results=[valid_field() for _ in range(3)] + [image_result()]
        ),
        rustfs=fetcher,
    )
    snap = snapshot_of(
        before_attachments=[{"object_key": "before/1.png", "file_name": "1.png"}],
        after_attachments=[{"object_key": "after/1.png", "file_name": "1.png"}],
    )
    mc = await pipe.run(snap, None)
    ic = mc["image_compare"]
    assert ic["implementation_status"] == "DEGRADED"
    assert ic["verdict"] == "SKIPPED"
    assert "连接失败" in (ic["reason"] or "")
    assert "ConnectError" in (ic["reason"] or "")


async def test_fetch_bytes_transport_error_wrapped() -> None:
    """单测锁定包装行为：TransportError → ModelCallError（保留原始异常链）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    cfg = RustFSSettings(
        endpoint="http://rustfs.test",
        access_key="ak",
        secret_key="sk",
        bucket="cps-attachments",
    )
    fetcher = RustFSObjectFetcher(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelCallError) as ei:
        await fetcher.fetch_bytes("any/object.png")
    assert "连接失败" in str(ei.value)
    assert isinstance(ei.value.__cause__, httpx.TransportError)


async def test_unexpected_runner_error_marks_failed_with_callback() -> None:
    """兜底 except Exception：runner 抛 RuntimeError → FAILED+CHECK_ERROR + 回调送达，
    不裸抛 500、行不卡 RUNNING。"""

    async def boom(row, request):
        raise RuntimeError("runner exploded unexpectedly")

    sf = await make_session_factory()
    service, callback, pending = make_service(sf, check_runner=boom)
    resp = await service.submit(make_request())
    # submit 响应精简（无 error_code），以回调 body 为准验证 CHECK_ERROR
    assert resp["status"] == "FAILED"
    await drain(pending)
    assert len(callback.payloads) == 1
    assert callback.payloads[0]["status"] == "FAILED"
    assert callback.payloads[0]["error_code"] == "CHECK_ERROR"
    assert "RuntimeError" in (callback.payloads[0].get("error") or "")
