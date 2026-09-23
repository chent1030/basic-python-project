"""Java 端 coverage 聚合查询 HTTP 客户端。

- 与 :mod:`app.skill.memory.infrastructure.java_client` 同模式（共享 httpx 客户端池 /
  指数退避 / 分页循环），但路径前缀不同（``/api/cps/admin/coverage/``）。
- 四个端点：``frequency`` / ``region-supervisor`` / ``recurrence`` / ``gaps``。
- 分页：默认 size=100，逐页翻完 yield 全量 dict。

Java 端契约（基线 cps 仓已落，FR-10 聚合查询端点）：
- GET /api/cps/admin/coverage/frequency?start=&end=&factory=&area=&categoryL1Id=&page=&size=
  → ``{items, total_pages}``，items = [
        {factory, area, category_l1_id, category_l2_id,
         issue_count, recent_count, closed_count, close_rate,
         open_count, handled_count, overdue_count,
         recurrence_count, last_recurrence_at, last_record_at}
    ]
- GET /api/cps/admin/coverage/region-supervisor → ``{items, total_pages}``，
  items = [{region, supervisor_emp_no, open_count, overdue_count, handled_count}]
- GET /api/cps/admin/coverage/recurrence?threshold=N → ``{items, total_pages}``，
  items = [{issue_id, recurrence_count, last_recurrence_at, category_l1_id, factory, area}]
- GET /api/cps/admin/coverage/gaps → ``{items, total_pages}``，
  items = [{storage_room_type, days_since_last_record, gap_severity,
            category_l1_id, factory, area}]
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.services.http_client import http_client

logger = logging.getLogger(__name__)


COVERAGE_BACKEND_URL_ENV: str = "CPS_BACKEND_URL"
DEFAULT_COVERAGE_BASE_URL: str = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_RETRIES: int = 2
DEFAULT_BACKOFFS: tuple[float, ...] = (1.0, 2.0)
DEFAULT_PAGE_SIZE: int = 100


class JavaCoverageFetchError(Exception):
    """Java 端 coverage 拉取失败（重试耗尽）。"""


class CoverageClient:
    """Java 端 ``/api/cps/admin/coverage/{kind}`` 拉取客户端。

    使用同 :class:`CpsMemoryClient` 的指数退避 + 分页循环契约，仅路径前缀不同。
    """

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

        env_url = os.environ.get(COVERAGE_BACKEND_URL_ENV)
        resolved = base_url or env_url or DEFAULT_COVERAGE_BASE_URL
        self._base_url = resolved.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoffs = backoffs
        self._transport = transport  # 测试注入 httpx.MockTransport

    @property
    def base_url(self) -> str:
        return self._base_url

    # -------------------------------------------------------------- public ----

    async def fetch_frequency(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start_iso,
            "end": end_iso,
            "page": page,
            "size": size,
        }
        if factory:
            params["factory"] = factory
        if area:
            params["area"] = area
        if category_l1_id is not None:
            params["categoryL1Id"] = category_l1_id
        return await self._fetch("/api/cps/admin/coverage/frequency", params)

    async def fetch_region_supervisor(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
        region: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start_iso,
            "end": end_iso,
            "page": page,
            "size": size,
        }
        if region:
            params["region"] = region
        return await self._fetch(
            "/api/cps/admin/coverage/region-supervisor", params,
        )

    async def fetch_recurrence(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
        threshold: int = 2,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start_iso,
            "end": end_iso,
            "page": page,
            "size": size,
            "threshold": threshold,
        }
        return await self._fetch("/api/cps/admin/coverage/recurrence", params)

    async def fetch_gaps(
        self,
        start_iso: str,
        end_iso: str,
        *,
        page: int = 1,
        size: int = DEFAULT_PAGE_SIZE,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start_iso,
            "end": end_iso,
            "page": page,
            "size": size,
        }
        if factory:
            params["factory"] = factory
        if area:
            params["area"] = area
        if category_l1_id is not None:
            params["categoryL1Id"] = category_l1_id
        return await self._fetch("/api/cps/admin/coverage/gaps", params)

    # -------------------------------------------------------------- helpers ----

    async def iter_all_pages(
        self,
        fetch_one: Any,
        start_iso: str,
        end_iso: str,
        *,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        """按 total_pages 自动循环翻页，逐页 yield。"""
        page = 1
        while True:
            page_resp = await fetch_one(start_iso, end_iso, page=page, size=size)
            yield page_resp
            total_pages = int(page_resp.get("total_pages") or 0)
            page += 1
            if page > total_pages or total_pages <= 0:
                break

    async def _fetch(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """带指数退避重试的 GET。返回解析后的 JSON dict；失败抛 ``JavaCoverageFetchError``。"""
        url = f"{self._base_url}{path}"
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
                    resp_obj = await http_client.get(
                        url, params=params, timeout=self._timeout,
                    )
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
        raise JavaCoverageFetchError(
            f"Java 端 coverage 拉取失败（path={path}, attempts={attempts}）：{last_err}"
        )


__all__ = [
    "COVERAGE_BACKEND_URL_ENV",
    "CoverageClient",
    "DEFAULT_COVERAGE_BASE_URL",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "JavaCoverageFetchError",
]