"""Effect metric Java client（FR-12）。

调 Java ``/api/cps/admin/coverage/effect`` 端点（与 CoverageClient 同模式），
返回 ``{items, total_pages, page, size}``。``items[i]`` 形如：

    {"periodStart": "...", "periodEnd": "...", "scopeKey": "...",
     "aiPassRate": 0.85, "humanOverrideRate": 0.12,
     "recurrenceRate30d": 0.05, "coverageGapCount": 7}
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


class JavaEffectFetchError(RuntimeError):
    """Effect metric 端点重试耗尽/超时等不可恢复错误。"""


class EffectMetricClient:
    """Java 端 ``/api/cps/admin/coverage/effect`` 客户端。"""

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
        url = f"{self._base_url}{path}"
        attempts = self._max_retries + 1
        last_error: Exception | None = None
        for i in range(attempts):
            try:
                response = await http_client.get(
                    url, params=params, timeout=self._timeout, transport=self._transport,
                )
                if response.status_code >= 500:
                    raise JavaEffectFetchError(
                        f"effect {path} 5xx={response.status_code}",
                    )
                if response.status_code >= 400:
                    raise JavaEffectFetchError(
                        f"effect {path} 4xx={response.status_code}",
                    )
                return response.json() or {}
            except (JavaEffectFetchError, TimeoutError) as exc:
                last_error = exc
                if i >= self._max_retries:
                    break
                backoff = self._backoffs[min(i, len(self._backoffs) - 1)]
                logger.warning(
                    "EffectMetricClient fetch %s attempt %d failed: %s; sleep %.2fs",
                    path, i + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if i >= self._max_retries:
                    break
                backoff = self._backoffs[min(i, len(self._backoffs) - 1)]
                logger.warning(
                    "EffectMetricClient fetch %s attempt %d error: %s; sleep %.2fs",
                    path, i + 1, exc, backoff,
                )
                await asyncio.sleep(backoff)
        raise JavaEffectFetchError(
            f"effect {path} 重试耗尽: {last_error!r}",
        )

    async def fetch_effect(
        self,
        start: str,
        end: str,
        *,
        page: int = 1,
        size: int = 100,
        scope_key: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start, "end": end, "page": page, "size": size,
        }
        if scope_key is not None:
            params["scopeKey"] = scope_key
        return await self._fetch("/api/cps/admin/coverage/effect", params=params)

    async def iter_all_pages(
        self,
        start: str,
        end: str,
        *,
        size: int = 100,
        scope_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        page = 1
        while True:
            resp = await self.fetch_effect(
                start, end, page=page, size=size, scope_key=scope_key,
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
    "EffectMetricClient",
    "JavaEffectFetchError",
]