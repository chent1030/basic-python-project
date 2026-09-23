"""契约 C-01/C-03 端点（Java → Python）+ 回调重推（波次 8，管理员）。

路径：挂在 api_router（前缀 /api/v1）下的 ``/agent`` 前缀，即
- C-01 POST /api/v1/agent/rectifications
- C-03 GET  /api/v1/agent/rectifications/{task_id}
- 重推 POST /api/v1/agent/rectifications/{task_id}/callback/re-push

Java 侧 CpsAgentFrameworkClient.baseUrl 已含 /api/v1，相对路径 /agent/rectifications 即命中。

鉴权：复用 agent_runs 的 principal（内网信任 → legacy-cps 全角色；或 bearer JWT）。
机器调用方（Java）来自 127.0.0.1 → 走内网信任，要求 cps_admin 角色。
（口径待联调：正式鉴权方案见 Phase 0 报告遗留项。）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.core.datasource import DatasourceManager
from app.core.datasource import datasources as shared_datasources
from app.projects.initial_review.application.service import (
    CallbackPushBudgetExhausted,
    InitialReviewService,
    ReplayConflict,
    TaskNotTerminal,
)
from app.projects.initial_review.domain.models import RectificationReviewRequest
from app.projects.initial_review.infrastructure.callback import JavaCallbackClient
from app.projects.initial_review.infrastructure.config import load_initial_review_settings
from app.projects.initial_review.infrastructure.model_client import (
    DashScopeModelClient,
    RustFSObjectFetcher,
)
from app.projects.initial_review.infrastructure.repository import InitialReviewRepository

router = APIRouter(prefix="/agent", tags=["agent-initial-review"], route_class=FrameworkRoute)


def initial_review_service(request: Request) -> InitialReviewService:
    """懒建服务实例并挂 app.state（数据源在 startup 时才可用）。"""
    service = getattr(request.app.state, "initial_review_service", None)
    if service is None:
        # J0 联调修正：DatasourceManager 是模块级单例（app/main.py 同源导入），
        # 不在 app.state 上；原先读 request.app.state.datasources 会 KeyError。
        manager: DatasourceManager = shared_datasources
        try:
            session_factory = manager.get_session_factory("postgres_primary")
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="postgres_primary 数据源未配置，初审服务不可用",
            ) from None
        settings = load_initial_review_settings()
        repo = InitialReviewRepository()

        async def load_history(issue_id: str, version_no: int) -> list[dict]:
            """A6 历史比对：读本库同 issue 早期 COMPLETED 版本（独立会话，不占执行事务）。"""
            async with session_factory() as session:
                return await repo.history_versions(session, issue_id, version_no)

        service = InitialReviewService(
            session_factory=session_factory,
            callback_client=JavaCallbackClient(settings),
            settings=settings,
            model_client=DashScopeModelClient(settings.model_check),
            rustfs_fetcher=RustFSObjectFetcher(settings.rustfs),
            history_loader=load_history,
        )
        request.app.state.initial_review_service = service
    return service


ServiceDep = Annotated[InitialReviewService, Depends(initial_review_service)]


@router.post("/rectifications", status_code=status.HTTP_202_ACCEPTED)
async def submit_rectification(
    body: RectificationReviewRequest,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """C-01：创建初审任务（幂等：同 cps-rectify-{issueId}-v{n} 返回同一 task_ref）。"""
    identity.require("cps_admin")
    try:
        return await service.submit(body)
    except ReplayConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/rectifications/{task_id}")
async def get_rectification_status(
    task_id: str,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """C-03：Java 兜底轮询任务状态（三态判定在 Java 侧，本端点提供事实数据）。"""
    identity.require("cps_admin")
    result = await service.status(task_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"初审任务不存在：{task_id}",
        )
    return result


@router.post("/rectifications/{task_id}/callback/re-push")
async def repush_rectification_callback(
    task_id: str,
    identity: Identity,
    service: ServiceDep,
) -> dict[str, Any]:
    """波次 8（J 线转交 #4）：手动重推终态任务的 C-02 回调（管理员用）。

    - 按 task_ref 查注册表行；行不存在 → 404；仍在跑（RUNNING）→ 409；
    - 同步等待发送并返回结果（pushed/callback_status/attempts/last_error）；
    - 配额：callback_attempts 累计达上限（默认 20）→ 429，防滥用；
    - 回调 Body 幂等键不变（initial-review-result-{task_id}），Java 端天然去重。
    """
    identity.require("cps_admin")
    try:
        result = await service.repush_callback(task_id)
    except TaskNotTerminal as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CallbackPushBudgetExhausted as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"初审任务不存在：{task_id}",
        )
    return result
