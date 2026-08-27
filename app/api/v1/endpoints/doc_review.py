"""Document review HTTP endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from app.core.logging_config import get_logger
from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review.service import run_doc_review

log = get_logger("app.api.doc_review")
router = APIRouter(prefix="/doc-review", tags=["doc-review"])


@router.post("")
async def submit_review(
    body: EhsConstruct,
    initiation_time: datetime | None = Query(
        default=None,
        description="流程发起时间；优先于body.processInitiatedAt",
    ),
) -> dict:
    """Synchronously review the construction documents in one application."""
    log.info("收到审核请求 workPermitNo=%s", body.workPermitNo)
    return await run_doc_review(body, initiation_time=initiation_time)
