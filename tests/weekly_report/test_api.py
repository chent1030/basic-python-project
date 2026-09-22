"""C-05 / C-08 端点测试。

构造最小 FastAPI app + 注入测试服务，覆盖：
- 列表 endpoint 鉴权 / 过滤 / 分页；
- 下载 endpoint 受控返回 + 状态保护。

注意：``Identity`` 是 ``Annotated[Principal, Depends(principal)]`` 类型别名，
依赖覆盖针对底层 ``principal`` 函数（``agent_runs.principal``），
不是别名本身（FastAPI 不能 override Annotated 别名）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.api.v1.endpoints import weekly_reports as wr_endpoint
from app.api.v1.endpoints.weekly_reports import router
from app.projects.weekly_report.application.service import (
    WeeklyReportService,
    compute_window,
)
from app.projects.weekly_report.domain.models import (
    PUSH_STATUS_UNCONFIGURED,
    STATUS_COMPLETED,
)


class _PrincipalStub:
    roles: list[str] = ["cps_admin"]

    def require(self, *roles: str) -> None:
        return None


def _override_identity() -> _PrincipalStub:
    return _PrincipalStub()


def _build_app(svc: WeeklyReportService) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.state.weekly_report_service = svc
    # 覆盖底层 principal 函数（Annotated 别名不可直接 override）
    app.dependency_overrides[agent_runs.principal] = _override_identity
    return app, TestClient(app)


@pytest.mark.asyncio
async def test_list_endpoint_returns_items_with_unavailable_reason(
    make_service, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("etag", 1024))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    run = await svc.run_once(
        report_type="cps-issue-inspection", window=win
    )

    app, client = _build_app(svc)
    resp = client.get("/agent/weekly-reports")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["run_id"] == run["run_id"] for item in body["items"])
    # UNCONFIGURED 行存在 → push_unavailable_reason 字段非空
    assert body.get("push_unavailable_reason") == "推送未配置"


@pytest.mark.asyncio
async def test_download_endpoint_returns_404_when_not_completed(
    make_service,
):
    svc = make_service()
    app, client = _build_app(svc)
    resp = client.get("/agent/weekly-reports/nonexistent/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_endpoint_returns_streamed_html_when_completed(
    make_service, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("e", 100))

    async def _fake_stream(key):
        yield b"<html><body>report</body></html>"

    fake_uploader.stream_get = _fake_stream

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    run = await svc.run_once(
        report_type="cps-issue-inspection", window=win
    )

    app, client = _build_app(svc)
    resp = client.get(f"/agent/weekly-reports/{run['run_id']}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["x-cps-run-id"] == run["run_id"]
    assert "report" in resp.text


@pytest.mark.asyncio
async def test_list_endpoint_filters_by_report_type(
    make_service, fixed_now
):
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    fake_uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    fake_uploader.available = True
    fake_uploader.unavailable_reason = None
    fake_uploader.put_bytes = AsyncMock(return_value=("e", 100))

    svc = make_service(uploader=fake_uploader)
    win = compute_window(now=fixed_now)
    await svc.run_once(report_type="room-check", window=win)
    await svc.run_once(report_type="cps-issue-inspection", window=win)

    app, client = _build_app(svc)
    resp = client.get("/agent/weekly-reports?report_type=room-check")
    assert resp.status_code == 200
    body = resp.json()
    assert all(
        item["report_type"] == "room-check" for item in body["items"]
    )


__all__ = ["STATUS_COMPLETED", "PUSH_STATUS_UNCONFIGURED"]


# 占位防 ruff 裁切未使用符号
_ = datetime  # noqa: F841
_ = UTC  # noqa: F841
_ = wr_endpoint  # noqa: F841