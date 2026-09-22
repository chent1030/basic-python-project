"""契约 C-01/C-03 端点（Java → Python）。

路径：挂在 api_router（前缀 /api/v1）下的 ``/agent`` 前缀，即
- C-01 POST /api/v1/agent/rectifications
- C-03 GET  /api/v1/agent/rectifications/{task_id}

Java 侧 CpsAgentFrameworkClient.baseUrl 已含 /api/v1，相对路径 /agent/rectifications 即命中。

鉴权：复用 agent_runs 的 principal（内网信任 → legacy-cps 全角色；或 bearer JWT）。
机器调用方（Java）来自 127.0.0.1 → 走内网信任，要求 cps_admin 角色。
（口径待联调：正式鉴权方案见 Phase 0 报告遗留项。）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.core.datasource import DatasourceManager, datasources as shared_datasources
from app.projects.initial_review.application.service import (
    InitialReviewService,
    ReplayConflict,
)
from app.projects.initial_review.domain.models import RectificationReviewRequest
from app.projects.initial_review.infrastructure.callback import JavaCallbackClient
from app.projects.initial_review.infrastructure.config import load_initial_review_settings

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
        service = InitialReviewService(
            session_factory=session_factory,
            callback_client=JavaCallbackClient(settings),
            settings=settings,
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
