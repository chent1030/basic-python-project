"""New construction-plan-only review endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.models.ehs_contruct_item import EhsConstruct
from app.projects.construction_plan_review import ConstructionPlanReviewOrchestrator
from app.projects.construction_plan_review.models import ReviewConfig, ReviewReport

router = APIRouter(prefix="/construction-plan-review", tags=["construction-plan-review"])


@router.post("", response_model=ReviewReport)
async def review_construction_plan(
    body: EhsConstruct,
    provider: str | None = Query(default=None, description="识别 Provider，默认 qwen"),
    model: str | None = Query(default=None, description="识别模型，默认 qwen3.8"),
    fallback_provider: str | None = Query(default=None, description="可选备用 Provider"),
) -> ReviewReport:
    """Review only construction-programme previews from ``s3PreviewFileUrl``."""

    config_values: dict[str, str] = {}
    if provider:
        config_values["provider"] = provider
    if model:
        config_values["model"] = model
    if fallback_provider:
        config_values["fallback_provider"] = fallback_provider
    config = ReviewConfig(**config_values)
    return await ConstructionPlanReviewOrchestrator().run(body, config=config)


__all__ = ["router"]
