"""Async HTTP client utility — thin wrapper around httpx.AsyncClient.

Goals:
- One shared client + connection pool for the whole process (started in lifespan)
- Convenient top-level methods: get / post / put / patch / delete
- Sensible JSON defaults; still flexible (params / data / files / headers)
- Unified `HttpResponse` so callers don't depend on httpx directly

Typical usage from anywhere in the app:
    from app.services.http_client import http_client

    resp = await http_client.get("https://api.example.com/users/1")
    data = resp.json()
    resp.raise_for_status()

    resp = await http_client.post(url, json={"k": 1}, headers={"X-Trace": "abc"})
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger("app.http")


class HttpResponse:
    """Thin, framework-agnostic wrapper over httpx.Response.

    Exposes only what most app code needs, so the rest of the codebase doesn't
    import httpx directly. The raw response is available via `.raw` if needed.
    """

    __slots__ = ("_resp",)

    def __init__(self, resp: httpx.Response) -> None:
        self._resp = resp

    @property
    def raw(self) -> httpx.Response:
        return self._resp

    @property
    def status_code(self) -> int:
        return self._resp.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self._resp.headers

    @property
    def text(self) -> str:
        return self._resp.text

    @property
    def url(self) -> str:
        return str(self._resp.url)

    @property
    def ok(self) -> bool:
        return self._resp.is_success

    def json(self, **kwargs: Any) -> Any:
        return self._resp.json(**kwargs)

    def raise_for_status(self) -> HttpResponse:
        """Raise httpx.HTTPStatusError on 4xx/5xx; otherwise return self."""
        self._resp.raise_for_status()
        return self

    def __repr__(self) -> str:
        return f"<HttpResponse [{self.status_code}] {self.url}>"


class HttpClient:
    """Shared async HTTP client.

    Lifecycle:
        await http_client.startup()   # called once from app lifespan
        ...
        await http_client.shutdown()

    Per-request API mirrors httpx.AsyncClient so existing muscle memory works.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    # ---------- lifecycle ---------------------------------------------
    async def startup(self) -> None:
        cfg = settings.http
        limits = httpx.Limits(
            max_connections=cfg.max_connections,
            max_keepalive_connections=cfg.max_keepalive_connections,
        )
        # `verify=False` only when explicitly configured (e.g. self-signed dev).
        self._client = httpx.AsyncClient(
            timeout=cfg.timeout,
            limits=limits,
            headers=dict(cfg.default_headers),
            verify=cfg.verify,
        )
        log.info(
            "HTTP client ready (timeout=%ss, max_connections=%d).",
            cfg.timeout,
            cfg.max_connections,
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpClient used before startup() — check lifespan.")
        return self._client

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str] | None:
        if headers is None:
            return None
        # Caller headers win over defaults (no need to merge here; httpx already
        # merges per-request headers on top of client defaults).
        return headers

    # ---------- HTTP verbs --------------------------------------------
    async def request(
        self, method: str, url: str, **kwargs: Any
    ) -> HttpResponse:
        """Generic request. Any httpx.AsyncClient.request kwargs are accepted."""
        client = self._ensure()
        resp = await client.request(method, url, **kwargs)
        return HttpResponse(resp)

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("DELETE", url, **kwargs)

    # ---------- streaming (rarely needed) -----------------------------
    async def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.AsyncByteStream:
        """Return an async context manager for streamed downloads.

        Usage:
            async with http_client.stream("GET", big_url) as resp:
                async for chunk in resp.aiter_bytes():
                    ...
        """
        client = self._ensure()
        return client.stream(method, url, **kwargs)


# Singleton — started/stopped from app lifespan.
http_client = HttpClient()


__all__ = ["HttpClient", "HttpResponse", "http_client"]
