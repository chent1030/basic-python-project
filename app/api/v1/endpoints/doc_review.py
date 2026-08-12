"""文档智能审核端点。

POST /api/v1/doc-review  接收 EhsConstruct,对每个文件跑审核 pipeline,返回报告。
POST /api/v1/doc-review/single  单文件审核(直接传 entity + url)。
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging_config import get_logger
from app.projects.doc_review.agents import DocReviewFlow
from app.projects.doc_review.service import run_doc_review

log = get_logger("app.api.doc_review")

router = APIRouter(prefix="/doc-review", tags=["doc-review"])


@router.post("")
async def submit_review(body: dict[str, Any]) -> dict[str, Any]:
    """提交文档审核(同步)。接收 EhsConstruct dict,审核每个文件,返回汇总报告。"""
    log.info("收到审核请求 entity_keys=%s", list(body)[:10])
    return await run_doc_review(body)


class SingleReviewIn(BaseModel):
    entity: dict[str, Any] = Field(..., description="业务数据")
    url: str = Field(..., description="文件 URL")


@router.post("/single")
async def submit_single(body: SingleReviewIn) -> dict[str, Any]:
    """单文件审核。"""
    entity_json = json.dumps(body.entity, ensure_ascii=False)
    message = f"业务数据:\n{entity_json}\n\n文件 URL: {body.url}"
    flow = DocReviewFlow()
    result = await flow.run(message)

    m = re.search(r"\{.*\}", result.output, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": result.output[:500], "overall_pass": None}
