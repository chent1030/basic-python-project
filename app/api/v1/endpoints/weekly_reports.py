"""C-05 / C-08 端点 — 周报列表 + 受控下载。

- C-05 GET /api/v1/agent/weekly-reports：分页列表（type/period/status/push_status 过滤）；
- C-08 GET /api/v1/agent/weekly-reports/{run_id}/download：受控流式返回归档字节。

鉴权：复用 agent_runs 的 FrameworkRoute + principal + Identity.require('cps_admin')，
与 C-01 / C-03 同源（设计 §5 / AC-04：管理端查询下载走 cps_admin 角色）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.core.datasource import DatasourceManager
from app.core.datasource import datasources as shared_datasources
from app.projects.weekly_report.application.service import WeeklyReportService
from app.projects.weekly_report.domain.data_fetcher import StubDataFetcher
from app.projects.weekly_report.infrastructure.config import (
    load_weekly_report_settings,
)

router = APIRouter(
    prefix="/agent/weekly-reports",
    tags=["agent-weekly-reports"],
    route_class=FrameworkRoute,
)

logger = logging.getLogger(__name__)


def _weekly_report_service(request: Request) -> WeeklyReportService:
    """懒建服务实例并挂 app.state（数据源 startup 后才可用）。"""
    service = getattr(request.app.state, "weekly_report_service", None)
    if service is None:
        manager: DatasourceManager = shared_datasources
        try:
            session_factory = manager.get_session_factory("postgres_primary")
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="postgres_primary 数据源未配置，周报服务不可用",
            ) from None
        settings = load_weekly_report_settings()
        service = WeeklyReportService(
            session_factory=session_factory,
            data_fetcher=StubDataFetcher(),
            settings=settings,
        )
        request.app.state.weekly_report_service = service
    return service


ServiceDep = Annotated[WeeklyReportService, Depends(_weekly_report_service)]


@router.get("", response_model=None)
async def list_weekly_reports(
    identity: Identity,
    service: ServiceDep,
    report_type: str | None = Query(default=None, max_length=64),
    period: str | None = Query(
        default=None,
        pattern=r"^\d{4}-W\d{2}$",
        description="ISO 周编号 yyyy-Ww",
    ),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(PENDING|RUNNING|ARCHIVING|COMPLETED|FAILED)$",
    ),
    push_status: str | None = Query(
        default=None,
        pattern="^(PENDING|SUCCESS|FAILED|SKIPPED|UNCONFIGURED)$",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> dict[str, Any]:
    """C-05 — 周报列表（分页 + 过滤）。"""
    identity.require("cps_admin")
    result = await service.list_runs(
        report_type=report_type,
        period=period,
        status=status_filter,
        push_status=push_status,
        limit=limit,
        offset=offset,
    )
    return result


@router.get("/{run_id}/download")
async def download_weekly_report(
    run_id: str,
    identity: Identity,
    service: ServiceDep,
) -> StreamingResponse:
    """C-08 — 受控流式下载归档 HTML。"""
    identity.require("cps_admin")
    pair = await service.get_run_for_download(run_id)
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"周报不存在或未完成：{run_id}",
        )
    row, body = pair
    filename = f"{row.report_type}-{row.period}-{row.run_no}.html"

    def _iter(chunks: bytes = body, chunk_size: int = 64 * 1024):  # noqa: B008
        for i in range(0, len(chunks), chunk_size):
            yield chunks[i : i + chunk_size]

    headers = {
        "Content-Length": str(len(body)),
        "X-Cps-Run-Id": row.run_id,
        "X-Cps-Period": row.period,
        "X-Cps-Report-Type": row.report_type,
        "Content-Disposition": (
            f'attachment; filename="{filename}"'
        ),
    }
    return StreamingResponse(
        _iter(),
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


__all__ = ["router"]


# 占位防 ruff 裁切未使用符号
_ = json  # noqa: F841
_ = Callable  # noqa: F841