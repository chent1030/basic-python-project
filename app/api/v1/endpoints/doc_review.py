"""文档智能审核端点 —— 接收外部系统请求,异步审核,回调返回结果。

流程:外部系统 POST {entity, url} → 立即返回 review_id → 后台审核完成 → 回调 callback_url

- POST /doc-review         提交审核(异步,立即返回 review_id)
- GET  /doc-review/{id}    查询审核状态(第一期:内存存储,重启丢失)
- POST /doc-review/sync    同步审核(等待完成返回结果,适合短文档/测试)
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging_config import get_logger
from app.services.doc_review import run_review

log = get_logger("app.api.doc_review")

router = APIRouter(prefix="/doc-review", tags=["doc-review"])

# 内存存储审核结果(第一期;生产可换 DB)
_reviews: dict[str, dict[str, Any]] = {}


class DocReviewIn(BaseModel):
    """审核请求。"""

    entity: dict[str, Any] = Field(
        ..., description="业务数据:供应商名、人员、项目信息等(比对基准)"
    )
    url: str = Field(..., description="待审核文件的 URL 地址")
    callback_url: str | None = Field(None, description="审核完成后的回调地址(可选)")


class DocReviewOut(BaseModel):
    review_id: str
    status: str


@router.post("", response_model=DocReviewOut)
async def submit_review(body: DocReviewIn) -> DocReviewOut:
    """提交文档审核(异步)。立即返回 review_id,审核完成后回调 callback_url。"""
    review_id = uuid.uuid4().hex
    _reviews[review_id] = {"review_id": review_id, "status": "processing"}

    # 后台异步执行(不阻塞响应)
    asyncio.create_task(_run_and_store(review_id, body))

    log.info(
        "提交审核 review_id=%s url=%s callback=%s",
        review_id, body.url, bool(body.callback_url),
    )
    return DocReviewOut(review_id=review_id, status="processing")


@router.post("/sync")
async def sync_review(body: DocReviewIn) -> dict[str, Any]:
    """同步审核(等待完成返回完整结果)。适合短文档或测试。"""
    review_id = uuid.uuid4().hex
    result = await run_review(body.entity, body.url, review_id, body.callback_url)
    return result


@router.get("/{review_id}")
async def get_review(review_id: str) -> dict[str, Any]:
    """查询审核状态/结果。"""
    if review_id not in _reviews:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"审核任务 '{review_id}' 不存在")
    return _reviews[review_id]


async def _run_and_store(review_id: str, body: DocReviewIn) -> None:
    """后台执行审核,结果存入内存。"""
    try:
        result = await run_review(body.entity, body.url, review_id, body.callback_url)
        _reviews[review_id] = result
    except Exception as e:
        log.exception("审核任务失败 review_id=%s", review_id)
        _reviews[review_id] = {
            "review_id": review_id,
            "status": "failed",
            "error": str(e),
        }
