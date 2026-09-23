"""Procedural Java client（FR-11）。

调 Java ``/api/cps/admin/memory/dispatcher`` 端点（按 CoverageClient 同模式）：

- 启动重试 + 指数退避；
- 翻页通过 ``iter_all_pages`` 暴露 async generator（service 侧聚合）；
- ``fetch_dispatcher`` 走 ``factory / area / categoryL1Id / categoryL2Id`` query
  映射；返回 ``{items, total_pages, page, size}``。
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from app.services import http_client

logger = logging.getLogger(__name__)


CPS_BACKEND_URL_ENV: str = "CPS_BACKEND_URL"
DEFAULT_CPS_BACKEND_URL: str = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT_SECONDS: float = 30.0
DEFAULT_MAX_RETRIES: int = 2
DEFAULT_BACKOFFS: tuple[float, ...] = (1.0, 2.0)


class JavaProceduralFetchError(RuntimeError):
    """Procedural dispatcher 端点重试耗尽/超时等不可恢复错误。"""


class ProceduralClient:
    """Java 端 ``/api/cps/admin/memory/dispatcher`` 客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoffs: tuple[float, ...] = DEFAULT_BACKOFFS,
        transport: Any = None,
    ) -> None:
        self._base_url = (
            base_url or os.getenv(CPS_BACKEND_URL_ENV) or DEFAULT_CPS_BACKEND_URL
        ).rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoffs = backoffs
        self._transport = transport

    async def _fetch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """单次 HTTP GET + 指数退避。"""
        url = f"{self._base_url}{path}"
        attempts = self._max_retries + 1
        last_error: Exception | None = None
        for i in range(attempts):
            try:
                response = await http_client.get(
                    url,
                    params=params,
                    timeout=self._timeout,
                    transport=self._transport,
                )
                if response.status_code >= 500:
                    raise JavaProceduralFetchError(
                        f"dispatcher {path} 5xx={response.status_code}",
                    )
                if response.status_code >= 400:
                    raise JavaProceduralFetchError(
                        f"dispatcher {path} 4xx={response.status_code}",
                    )
                return response.json() or {}
            except (JavaProceduralFetchError, TimeoutError) as exc:
                last_error = exc
                if i >= self._max_retries:
                    break
                backoff = self._backoffs[min(i, len(self._backoffs) - 1)]
                logger.warning(
                    "ProceduralClient fetch %s attempt %d failed: %s; sleep %.2fs",
                    path, i + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if i >= self._max_retries:
                    break
                backoff = self._backoffs[min(i, len(self._backoffs) - 1)]
                logger.warning(
                    "ProceduralClient fetch %s attempt %d error: %s; sleep %.2fs",
                    path, i + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
        raise JavaProceduralFetchError(
            f"dispatcher {path} 重试耗尽: {last_error!r}",
        )

    async def fetch_dispatcher(
        self,
        start: str,
        end: str,
        *,
        page: int = 1,
        size: int = 100,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
        category_l2_id: int | None = None,
    ) -> dict[str, Any]:
        """拉单页（service 侧用 ``iter_all_pages`` 聚合）。"""
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "page": page,
            "size": size,
        }
        if factory is not None:
            params["factory"] = factory
        if area is not None:
            params["area"] = area
        if category_l1_id is not None:
            params["categoryL1Id"] = category_l1_id
        if category_l2_id is not None:
            params["categoryL2Id"] = category_l2_id
        return await self._fetch("/api/cps/admin/memory/dispatcher", params=params)

    async def iter_all_pages(
        self,
        start: str,
        end: str,
        *,
        size: int = 100,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
        category_l2_id: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """一直翻到 total_pages，每页是 dispatcher 返回的 dict。"""
        page = 1
        while True:
            resp = await self.fetch_dispatcher(
                start, end, page=page, size=size,
                factory=factory, area=area,
                category_l1_id=category_l1_id, category_l2_id=category_l2_id,
            )
            yield resp
            total_pages = int(resp.get("total_pages") or 1)
            if page >= total_pages:
                return
            page += 1


__all__ = [
    "CPS_BACKEND_URL_ENV",
    "DEFAULT_BACKOFFS",
    "DEFAULT_CPS_BACKEND_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "JavaProceduralFetchError",
    "ProceduralClient",
]