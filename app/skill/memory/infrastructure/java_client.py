"""记忆消费端 HTTP 客户端：拉取 Java 端 cps_review_adjudication / event / issue。

- ``CPS_BACKEND_URL``（默认 ``http://127.0.0.1:8080``），读环境变量；
- 复用 ``app.services.http_client`` 的共享连接池；
- 超时 30s，重试 2 次（指数退避：1s/2s），与 weekly_report pusher 同模式但**独立
  timeout**（pusher 走回调路径，本客户端走批量拉取，长一些更稳）；
- 分页循环：page=1..total_pages，size 默认 100；外部按 start/end 范围拉一次完整。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.services.http_client import http_client

logger = logging.getLogger(__name__)


CPS_BACKEND_URL_ENV: str = "CPS_BACKEND_URL"
DEFAULT_BACKEND_URL: str = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_RETRIES: int = 2
DEFAULT_BACKOFFS: tuple[float, ...] = (1.0, 2.0)
DEFAULT_PAGE_SIZE: int = 100


class CpsMemoryClient:
    """调用 Java 端 ``/api/cps/admin/memory/{kind}`` 拉取裁决/事件/问题。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        backoffs: tuple[float, ...] = DEFAULT_BACKOFFS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        import os

        env_url = os.environ.get(CPS_BACKEND_URL_ENV)
        resolved = base_url or env_url or DEFAULT_BACKEND_URL
        self._base_url = resolved.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoffs = backoffs
        self._transport = transport  # 测试注入 httpx.MockTransport

    @property
    def base_url(self) -> str:
        return self._base_url

    # -------------------------------------------------------------- public ----

    async def fetch_adjudications(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self._fetch(
            "/api/cps/admin/memory/adjudications", start_iso, end_iso, page, size,
        )

    async def fetch_events(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self._fetch(
            "/api/cps/admin/memory/events", start_iso, end_iso, page, size,
        )

    async def fetch_issues(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self._fetch(
            "/api/cps/admin/memory/issues", start_iso, end_iso, page, size,
        )

    # -------------------------------------------------------------- helpers ----

    async def iter_all_pages(
        self,
        fetch_one: Any,
        start_iso: str,
        end_iso: str,
        *,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        """按 total_pages 自动循环翻页，逐页 yield。

        fetch_one: ``await client.fetch_adjudications(start, end, page=page, size=size)``
        """
        page = 1
        while True:
            page_resp = await fetch_one(start_iso, end_iso, page=page, size=size)
            yield page_resp
            total_pages = int(page_resp.get("total_pages") or 0)
            page += 1
            if page > total_pages or total_pages <= 0:
                break

    async def _fetch(
        self, path: str, start_iso: str, end_iso: str, page: int, size: int
    ) -> dict[str, Any]:
        """带指数退避重试的 GET。返回解析后的 JSON dict；失败抛 ``JavaMemoryFetchError``。"""
        url = f"{self._base_url}{path}"
        params = {"start": start_iso, "end": end_iso, "page": page, "size": size}
        last_err: str | None = None
        attempts = 0
        max_attempts = 1 + max(self._max_retries, 0)
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                if self._transport is not None:
                    async with httpx.AsyncClient(
                        timeout=self._timeout, transport=self._transport
                    ) as client:
                        resp = await client.get(url, params=params)
                else:
                    resp_obj = await http_client.get(url, params=params, timeout=self._timeout)
                    resp = resp_obj.raw
                if 200 <= resp.status_code < 300:
                    return dict(resp.json() or {})
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except (TimeoutError, httpx.HTTPError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                if self._backoffs:
                    idx = min(attempt - 1, len(self._backoffs) - 1)
                    backoff = self._backoffs[idx]
                else:
                    backoff = 0.0
                if backoff > 0:
                    await asyncio.sleep(backoff)
        raise JavaMemoryFetchError(
            f"Java 端拉取失败（path={path}, attempts={attempts}）：{last_err}"
        )


class JavaMemoryFetchError(Exception):
    """Java 端拉取失败（重试耗尽）。"""


__all__ = [
    "CPS_BACKEND_URL_ENV",
    "CpsMemoryClient",
    "DEFAULT_BACKEND_URL",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "JavaMemoryFetchError",
]