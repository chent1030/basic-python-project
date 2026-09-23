"""记忆体系 API 路由（I-line 长期记忆 + FR-09）。

路径（挂在 api_router，前缀 /api/v1）：
- POST /memory/ingest/adjudication  单条裁决 ingestion（CPS 管理员）
- POST /memory/ingest/event         单条事件 ingestion
- POST /memory/ingest/issue         单条问题单 ingestion
- POST /memory/backfill             批量回填（start/end/kinds）
- GET  /memory/retrieve             按 issue 检索上下文
- GET  /memory/patterns             语义层 pattern 检索

鉴权：与 ``agent_callbacks`` 同模式，``Identity.require('cps_admin')``。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity
from app.core.datasource import DatasourceManager
from app.core.datasource import datasources as shared_datasources
from app.skill.memory.application import (
    IngestionSummary,
    MemoryClusteringService,
    MemoryIngestionService,
    MemoryRetrievalService,
)
from app.skill.memory.infrastructure.embedding_client import make_default_embedding_client
from app.skill.memory.infrastructure.java_client import CpsMemoryClient
from app.skill.memory.infrastructure.repository import MemoryRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["skill-memory"], route_class=FrameworkRoute)


# ---------------------------------------------------------------- request/response models --


class AdjudicationIngestRequest(BaseModel):
    """单条裁决 ingestion 请求体（Java callback 端会推送）。"""

    id: int = Field(..., description="cps_review_adjudication.id")
    issue_id: int | None = None
    version_no: int | None = None
    category_l1_id: int | None = None
    category_l2_id: int | None = None
    factory: str | None = None
    area: str | None = None
    severity: int | None = None
    decision: str | None = None
    ai_relation: str | None = None
    reason: str | None = None
    reviewer: str | None = None
    tags: list[str] | None = None
    payload: dict[str, Any] | None = None


class EventIngestRequest(BaseModel):
    id: int
    issue_id: int | None = None
    version_no: int | None = None
    category_l1_id: int | None = None
    category_l2_id: int | None = None
    factory: str | None = None
    area: str | None = None
    severity: int | None = None
    payload: dict[str, Any] | None = None
    tags: list[str] | None = None


class IssueIngestRequest(BaseModel):
    id: int
    version_no: int | None = None
    category_l1_id: int | None = None
    category_l2_id: int | None = None
    factory: str | None = None
    area: str | None = None
    severity: int | None = None
    payload: dict[str, Any] | None = None
    tags: list[str] | None = None


class BackfillRequest(BaseModel):
    startIso: str = Field(..., description="ISO 8601 起点")
    endIso: str = Field(..., description="ISO 8601 终点")
    kinds: list[str] | None = Field(
        default=None,
        description="adjudications/events/issues；缺省 = 全部",
    )


# ---------------------------------------------------------------- service locator --


def _memory_services(request: Request) -> tuple[
    MemoryRepository,
    MemoryIngestionService,
    MemoryRetrievalService,
    MemoryClusteringService,
]:
    """懒建服务实例并挂 app.state。"""
    cached = getattr(request.app.state, "memory_services", None)
    if cached is not None:
        return cached

    manager: DatasourceManager = shared_datasources
    try:
        session_factory = manager.get_session_factory("postgres_primary")
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="postgres_primary 数据源未配置，记忆服务不可用",
        ) from None

    repository = MemoryRepository(session_factory)
    embedding_client = make_default_embedding_client()
    java_client = CpsMemoryClient()
    ingestion = MemoryIngestionService(repository, embedding_client, java_client)
    retrieval = MemoryRetrievalService(repository, embedding_client)
    clustering = MemoryClusteringService(repository)
    services = (repository, ingestion, retrieval, clustering)
    request.app.state.memory_services = services
    return services


ServicesDep = Annotated[
    tuple[
        MemoryRepository,
        MemoryIngestionService,
        MemoryRetrievalService,
        MemoryClusteringService,
    ],
    Depends(_memory_services),
]


# ---------------------------------------------------------------- endpoints --


@router.post("/ingest/adjudication", status_code=status.HTTP_202_ACCEPTED)
async def ingest_adjudication(
    body: AdjudicationIngestRequest,
    identity: Identity,
    services: ServicesDep,
) -> dict[str, Any]:
    """单条裁决 ingestion（Java callback 入口）。"""
    identity.require("cps_admin")
    _repo, ingestion, _retrieval, _clustering = services
    saved = await ingestion.ingest_adjudication(body.model_dump())
    return {"id": saved.id, "source_table": saved.source_table, "source_id": saved.source_id}


@router.post("/ingest/event", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    body: EventIngestRequest,
    identity: Identity,
    services: ServicesDep,
) -> dict[str, Any]:
    """单条事件 ingestion。"""
    identity.require("cps_admin")
    _repo, ingestion, _retrieval, _clustering = services
    saved = await ingestion.ingest_event(body.model_dump())
    return {"id": saved.id, "source_table": saved.source_table, "source_id": saved.source_id}


@router.post("/ingest/issue", status_code=status.HTTP_202_ACCEPTED)
async def ingest_issue(
    body: IssueIngestRequest,
    identity: Identity,
    services: ServicesDep,
) -> dict[str, Any]:
    """单条问题单 ingestion。"""
    identity.require("cps_admin")
    _repo, ingestion, _retrieval, _clustering = services
    saved = await ingestion.ingest_issue(body.model_dump())
    return {"id": saved.id, "source_table": saved.source_table, "source_id": saved.source_id}


@router.post("/backfill", status_code=status.HTTP_202_ACCEPTED)
async def backfill(
    body: BackfillRequest,
    identity: Identity,
    services: ServicesDep,
) -> dict[str, Any]:
    """批量回填：从 Java 端拉取 → 逐条 ingest。"""
    identity.require("cps_admin")
    _repo, ingestion, _retrieval, _clustering = services
    summary: IngestionSummary = await ingestion.backfill_since(
        body.startIso, body.endIso, kinds=body.kinds
    )
    return summary.to_dict()


@router.get("/retrieve")
async def retrieve(
    identity: Identity,
    services: ServicesDep,
    issueJson: str = Query(..., description="JSON 序列化的 issue dto"),
    topK: int = Query(5, ge=1, le=50),
    kindFilter: str | None = Query(
        None,
        description="逗号分隔 source_table 过滤，如 'ADJUDICATION,EVENT'",
    ),
) -> dict[str, Any]:
    """按 issue 检索上下文（AI 初审 prompt 拼接用）。"""
    identity.require("cps_admin")
    import json as _json

    _repo, _ingestion, retrieval, _clustering = services
    try:
        issue = _json.loads(issueJson)
        if not isinstance(issue, dict):
            raise ValueError("issueJson 必须是 JSON 对象")
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"issueJson 解析失败: {exc}",
        ) from exc
    kind_list: list[str] | None = None
    if kindFilter:
        kind_list = [k.strip() for k in kindFilter.split(",") if k.strip()]
    hints = await retrieval.retrieve_context_for_issue(
        issue, top_k=topK, kind_filter=kind_list
    )
    return {
        "hints": [h.to_prompt_dict() for h in hints],
        "prompt_payload": retrieval.format_hints_for_prompt(hints),
    }


@router.get("/patterns")
async def list_patterns(
    identity: Identity,
    services: ServicesDep,
    scenario: str = Query(..., min_length=1, max_length=512),
    topK: int = Query(3, ge=1, le=20),
) -> dict[str, Any]:
    """语义层 pattern 检索（AI 初审「历史同类如何处理」提示）。"""
    identity.require("cps_admin")
    _repo, _ingestion, retrieval, _clustering = services
    hints = await retrieval.retrieve_pattern_hint(scenario, top_k=topK)
    return {
        "patterns": [
            {
                "skill_code": h.entry.payload.get("skill_code"),
                "pattern_summary": h.entry.payload.get("pattern_summary"),
                "example_count": h.entry.payload.get("example_count"),
                "score": h.score,
            }
            for h in hints
        ],
    }


__all__ = ["router"]