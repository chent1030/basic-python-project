"""C7 端到端装配测试 — 周报全链（调度遍历→双分类→归档→推送 UNCONFIGURED）
+ C-05/C-08 契约 schema 冻结（与全链同夹具内联锁定）。

测试金字塔标注：
- 本文件为**集成级**测试（sqlite 内存库 + fake uploader/pusher + 真实服务编排）；
- APScheduler 真调度、真实 RustFS 归档、真实推送渠道属**冒烟级**，
  见 scripts/c3_weekly_report_smoke.py（需真实环境，不进 pytest）。

「调度」环节在测试中以 weekly_report_run 任务的等价遍历模拟
（app/tasks/weekly_report_task.py: for report_type in settings.report_types），
因生产入口依赖 postgres_primary 数据源装配。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import agent_runs
from app.api.v1.endpoints.weekly_reports import router as wr_router
from app.projects.weekly_report.application.service import (
    WeeklyReportService,
    compute_window,
)
from app.projects.weekly_report.domain.models import (
    PUSH_STATUS_UNCONFIGURED,
    STATUS_COMPLETED,
)
from app.projects.weekly_report.infrastructure.rustfs_uploader import (
    RustFSWeeklyReportUploader,
)

#: C-05 GET /agent/weekly-reports → 200 外层字段集（冻结基线）
C05_RESPONSE_KEYS = {
    "items",
    "total",
    "limit",
    "offset",
    "push_unavailable_reason",  # AC-29：未配置推送必须可见
}

#: C-05 items[] 元素字段集（冻结基线）
C05_ITEM_KEYS = {
    "run_id",
    "report_type",
    "period",
    "window_start",
    "window_end",
    "run_no",
    "status",
    "push_status",
    "archive_object_key",
    "archive_bytes",
    "started_at",
    "finished_at",
    "error_code",
}


def _make_uploader(body: bytes = b"<html><body>report</body></html>"):
    """fake uploader：put_bytes 成功 + stream_get 回放同一字节。"""
    uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    uploader.available = True
    uploader.unavailable_reason = None
    uploader.put_bytes = AsyncMock(return_value=("etag-e2e", len(body)))

    async def _stream(key):  # type: ignore[no-untyped-def]
        yield body

    uploader.stream_get = _stream
    return uploader, body


def _build_app(svc: WeeklyReportService) -> TestClient:
    app = FastAPI()
    app.include_router(wr_router)
    app.state.weekly_report_service = svc
    app.dependency_overrides[agent_runs.principal] = (
        lambda: type("P", (), {"roles": ["cps_admin"], "require": lambda self, *a: None})()
    )
    return TestClient(app)


async def test_weekly_report_dual_type_full_chain(make_service, fixed_now):
    """全链装配：调度遍历双分类 → 各自渲染归档 → 推送恒 UNCONFIGURED。

    锁定（主计划 C7 / AC-04/28/29）：
    - 两类型同窗口各生成一条 COMPLETED 运行（run_no 各自从 1 起）；
    - 归档键按类型隔离 weekly-reports/{report_type}/…；
    - push_status=UNCONFIGURED（推送成功 ≠ 上传成功，未配置不得伪装成功）；
    - C-05 列表可见两类型且 push_unavailable_reason 非空。
    """
    uploader, _ = _make_uploader()
    svc = make_service(uploader=uploader)
    win = compute_window(now=fixed_now)

    # —— 调度任务等价遍历（weekly_report_run 的循环体）——
    results = [
        await svc.run_once(report_type=rt, window=win)
        for rt in svc.settings.report_types
    ]
    assert len(results) == 2
    by_type = {r["report_type"]: r for r in results}

    for rt, r in by_type.items():
        assert r["status"] == STATUS_COMPLETED, rt
        assert r["push_status"] == PUSH_STATUS_UNCONFIGURED
        assert r["archive_object_key"].startswith(f"weekly-reports/{rt}/")

    # 归档键按类型隔离（同名窗口不同前缀）
    keys = {r["archive_object_key"] for r in results}
    assert len(keys) == 2

    # —— C-05 列表两类型可见 ——
    client = _build_app(svc)
    resp = client.get("/agent/weekly-reports")
    assert resp.status_code == 200
    body_json = resp.json()
    assert set(body_json) == C05_RESPONSE_KEYS  # 外层 schema 冻结
    listed_types = {item["report_type"] for item in body_json["items"]}
    assert listed_types == set(svc.settings.report_types)
    for item in body_json["items"]:
        assert set(item) == C05_ITEM_KEYS  # 元素 schema 冻结
        assert item["run_no"] == 1  # 首跑：两类型各自从 1 起
        assert item["push_status"] == PUSH_STATUS_UNCONFIGURED
    assert body_json["total"] == 2
    # AC-29：有 UNCONFIGURED 行 → 推送未配置必须可见
    assert body_json["push_unavailable_reason"] == "推送未配置"


async def test_weekly_report_rerun_same_window_idempotent_both_types(
    make_service, fixed_now
):
    """同窗口重跑幂等：两类型重复 run_once 均 skipped，不产生第二条运行。"""
    uploader, _ = _make_uploader()
    svc = make_service(uploader=uploader)
    win = compute_window(now=fixed_now)

    types = ("cps-issue-inspection", "room-check")
    first = [await svc.run_once(report_type=rt, window=win) for rt in types]
    second = [await svc.run_once(report_type=rt, window=win) for rt in types]

    assert all(r["status"] == STATUS_COMPLETED for r in first)
    assert all(r.get("skipped") for r in second), second  # 同窗口 COMPLETED → 跳过

    client = _build_app(svc)
    body = client.get("/agent/weekly-reports").json()
    assert body["total"] == 2  # 幂等：没有新增运行


async def test_c08_download_headers_and_body_frozen(make_service, fixed_now):
    """C-08 契约冻结：下载响应头（X-Cps-* / Content-Disposition）+ 字节一致。"""
    body = b"<html><body>weekly</body></html>"
    uploader, _ = _make_uploader(body)
    svc = make_service(uploader=uploader)
    win = compute_window(now=fixed_now)
    run = await svc.run_once(report_type="cps-issue-inspection", window=win)

    client = _build_app(svc)
    resp = client.get(f"/agent/weekly-reports/{run['run_id']}/download")
    assert resp.status_code == 200
    headers = resp.headers
    assert headers["content-type"].startswith("text/html")
    assert headers["x-cps-run-id"] == run["run_id"]
    assert headers["x-cps-period"] == run["period"]
    assert headers["x-cps-report-type"] == "cps-issue-inspection"
    assert "attachment" in headers["content-disposition"]
    disposition = headers["content-disposition"]
    assert run["run_id"] in disposition or "cps-issue-inspection" in disposition
    assert resp.content == body  # 归档字节与流式下载一致


async def test_c08_download_rejects_uncompleted_run(make_service, fixed_now):
    """状态保护：非 COMPLETED（上传失败 FAILED）行不可下载 → 404。"""
    from app.projects.weekly_report.domain.models import STATUS_FAILED

    uploader = AsyncMock(spec=RustFSWeeklyReportUploader)
    uploader.available = False  # 触发 FAILED
    uploader.unavailable_reason = "rustfs down"
    svc = make_service(uploader=uploader)
    win = compute_window(now=fixed_now)
    run = await svc.run_once(report_type="cps-issue-inspection", window=win)
    assert run["status"] == STATUS_FAILED

    client = _build_app(svc)
    resp = client.get(f"/agent/weekly-reports/{run['run_id']}/download")
    assert resp.status_code == 404
