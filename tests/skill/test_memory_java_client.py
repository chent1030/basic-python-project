"""CpsMemoryClient 单元测试：分页循环 / 重试 / 超时（httpx.MockTransport 注入）。

不在本波做真实 Java 联调；只验证客户端契约（HTTP 调用形态、分页、重试、超时）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.skill.memory.infrastructure.java_client import (
    CPS_BACKEND_URL_ENV,
    CpsMemoryClient,
    JavaMemoryFetchError,
)


def _client_with_mock(transport: httpx.MockTransport, **kwargs) -> CpsMemoryClient:
    return CpsMemoryClient(
        base_url=kwargs.get("base_url", "http://java.test"),
        timeout=kwargs.get("timeout", 1.0),
        max_retries=kwargs.get("max_retries", 2),
        backoffs=kwargs.get("backoffs", (0.001, 0.001)),
        transport=transport,
    )


# ---------------------------------------------------------------- tests --


@pytest.mark.asyncio
async def test_iter_all_pages_loops_pages():
    """page=1/2 两页 → total_pages=2 → 翻完收手。"""
    pages = {
        1: {"items": [{"id": 1}, {"id": 2}], "total_pages": 2},
        2: {"items": [{"id": 3}], "total_pages": 2},
    }
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # request.url.params.get('page')
        page = int(request.url.params.get("page") or "1")
        calls.append((str(request.url), dict(request.url.params)))
        return httpx.Response(200, json=pages.get(page, {"items": [], "total_pages": 0}))

    client = _client_with_mock(httpx.MockTransport(handler))
    items: list[dict] = []
    async for page in client.iter_all_pages(client.fetch_adjudications, "2024-01-01", "2024-12-31"):
        items.extend(page.get("items") or [])
    assert len(items) == 3
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retry_on_5xx_then_success():
    """首次 503 → 第二次 200 → 应成功。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"items": [], "total_pages": 0})

    client = _client_with_mock(httpx.MockTransport(handler), max_retries=2)
    result = await client.fetch_adjudications("2024-01-01", "2024-12-31", page=1, size=10)
    assert result["items"] == []
    assert len(attempts) == 2  # 失败 1 次 + 成功 1 次


@pytest.mark.asyncio
async def test_retry_on_connection_error_then_success():
    """首次 httpx.ConnectError → 第二次 200 → 应成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if not hasattr(handler, "_called"):
            handler._called = True  # type: ignore[attr-defined]
            raise httpx.ConnectError("conn refused")
        return httpx.Response(200, json={"items": [{"id": 1}], "total_pages": 1})

    client = _client_with_mock(httpx.MockTransport(handler), max_retries=2)
    result = await client.fetch_adjudications("2024-01-01", "2024-12-31", page=1, size=10)
    assert result["items"] == [{"id": 1}]


@pytest.mark.asyncio
async def test_timeout_raises_java_fetch_error():
    """httpx.TimeoutException 累计 → JavaMemoryFetchError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = _client_with_mock(httpx.MockTransport(handler), max_retries=2, timeout=0.001)
    with pytest.raises(JavaMemoryFetchError):
        await client.fetch_adjudications("2024-01-01", "2024-12-31", page=1, size=10)


@pytest.mark.asyncio
async def test_url_construction_includes_path_and_query():
    """URL 形如 {base}/api/cps/admin/memory/adjudications?start=...&end=...&page=...&size=..."""
    seen_url: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_url["url"] = str(request.url)
        seen_url["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": [], "total_pages": 0})

    client = _client_with_mock(httpx.MockTransport(handler))
    await client.fetch_adjudications("S", "E", page=2, size=25)
    assert "/api/cps/admin/memory/adjudications" in seen_url["url"]
    assert seen_url["params"]["page"] == "2"
    assert seen_url["params"]["size"] == "25"


def test_default_base_url_uses_env(monkeypatch):
    monkeypatch.setenv(CPS_BACKEND_URL_ENV, "http://from-env:9999")
    c = CpsMemoryClient()
    assert c._base_url == "http://from-env:9999"  # type: ignore[attr-defined]


def test_default_base_url_fallback():
    import os

    for k in (CPS_BACKEND_URL_ENV,):
        os.environ.pop(k, None)
    c = CpsMemoryClient()
    assert c._base_url == "http://127.0.0.1:8080"  # type: ignore[attr-defined]


__all__ = []