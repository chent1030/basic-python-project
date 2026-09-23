"""CoverageClient 单测：四端点 round-trip / 分页 / 重试 / 超时。

注入 httpx.MockTransport 模拟 Java 端，避免起真服务器。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.skill.coverage.infrastructure.java_client import (
    CoverageClient,
    JavaCoverageFetchError,
)


def _ok_response(items, total_pages=1, page=1, size=100) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(
            {"items": items, "total_pages": total_pages, "page": page, "size": size},
        ).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://test/coverage"),
    )


@pytest.mark.asyncio
async def test_fetch_frequency_round_trip():
    """happy path：返回 items + total_pages=1 → fetch 直接拿回 list[dict]。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.path == "/api/cps/admin/coverage/frequency"
        return _ok_response([{"factory": "A", "area": "B", "category_l1_id": 1, "issue_count": 1}])

    transport = httpx.MockTransport(handler)
    client = CoverageClient(
        base_url="http://test", transport=transport, max_retries=0, backoffs=(0, 0),
    )
    resp = await client.fetch_frequency(
        "2024-01-01", "2024-01-31", factory="A", area="B", category_l1_id=1,
    )
    assert resp["items"][0]["factory"] == "A"
    # query 参数被正确序列化
    url = captured[0].url
    assert url.params["factory"] == "A"
    assert url.params["area"] == "B"
    assert url.params["categoryL1Id"] == "1"
    assert url.params["start"] == "2024-01-01"
    assert url.params["end"] == "2024-01-31"


@pytest.mark.asyncio
async def test_iter_all_pages_collects_all():
    """iter_all_pages 一直翻页直到 total_pages。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return _ok_response(
                items=[{"issue_id": i} for i in range(2)],
                total_pages=2, page=1, size=2,
            )
        if page == 2:
            return _ok_response(
                items=[{"issue_id": 99}],
                total_pages=2, page=2, size=2,
            )
        raise AssertionError("unexpected page")

    transport = httpx.MockTransport(handler)
    client = CoverageClient(
        base_url="http://test", transport=transport, max_retries=0, backoffs=(0, 0),
    )
    collected: list[dict] = []
    async for page_resp in client.iter_all_pages(
        client.fetch_recurrence, "2024-01-01", "2024-01-31",
    ):
        collected.extend(page_resp.get("items") or [])
    assert calls["n"] == 2
    assert len(collected) == 3
    assert collected[-1]["issue_id"] == 99


@pytest.mark.asyncio
async def test_retry_on_5xx_then_success():
    """先 503 后 200 → 应重试成功。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(503, content=b"boom", request=request)
        return _ok_response([{"ok": True}])

    transport = httpx.MockTransport(handler)
    client = CoverageClient(
        base_url="http://test", transport=transport, max_retries=2, backoffs=(0, 0),
    )
    resp = await client.fetch_gaps("2024-01-01", "2024-01-31")
    assert resp["items"][0]["ok"] is True
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises_java_coverage_fetch_error():
    """连续 5xx 超过 retries → 抛 JavaCoverageFetchError。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, content=b"boom", request=request)

    transport = httpx.MockTransport(handler)
    client = CoverageClient(
        base_url="http://test", transport=transport, max_retries=1, backoffs=(0, 0),
    )
    with pytest.raises(JavaCoverageFetchError):
        await client.fetch_region_supervisor("2024-01-01", "2024-01-31")
    # 1 + 1 次重试 = 2 次
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_fetch_recurrence_passes_threshold_as_query():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response([])

    transport = httpx.MockTransport(handler)
    client = CoverageClient(
        base_url="http://test", transport=transport, max_retries=0, backoffs=(0, 0),
    )
    await client.fetch_recurrence("2024-01-01", "2024-01-31", threshold=4)
    assert captured[0].url.params["threshold"] == "4"