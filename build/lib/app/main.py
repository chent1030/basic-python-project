"""FastAPI application entrypoint.

Wires together:
- lifespan: opens all datasource connections at startup, closes at shutdown
- exception handlers
- routers under /api/v1
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.datasource import datasources
from app.services.http_client import http_client
from app.services.llm import llm_client


def _configure_logging() -> None:
    logging.basicConfig(level=settings.logging.level, format=settings.logging.format)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: open every datasource + LLM client; Shutdown: close them."""
    log = logging.getLogger("app.lifespan")
    _configure_logging()

    log.info("Opening datasources: %s", datasources.names() if datasources.names() else "(none)")
    await datasources.startup()
    await llm_client.startup()
    await http_client.startup()
    log.info("All datasources, LLM client and HTTP client ready.")

    try:
        yield
    finally:
        log.info("Closing HTTP client, LLM client, datasources...")
        await http_client.shutdown()
        await llm_client.shutdown()
        await datasources.shutdown()
        log.info("Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app.name,
        debug=settings.app.debug,
        lifespan=lifespan,
    )

    # ---- exception handlers -----------------------------------------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logging.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    # ---- routes -----------------------------------------------------
    app.include_router(api_router, prefix=settings.app.api_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, object]:
        """Liveness + datasource readiness probe."""
        return {
            "status": "ok",
            "datasources": {
                name: datasources.kind(name) for name in datasources.names()
            },
        }

    return app


app = create_app()
