"""Aggregates all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent_callbacks,
    agent_runs,
    auth,
    chat,
    construction_plan_review,
    cps,
    doc_review,
    examples,
    inspection_plans,
    items,
    room_checks,
    tasks,
    weekly_reports,
)
from app.api.v1.endpoints import datasources as ds

api_router = APIRouter()
api_router.include_router(items.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(ds.router)
api_router.include_router(examples.router)
api_router.include_router(tasks.router)
api_router.include_router(doc_review.router)
api_router.include_router(agent_runs.router)
api_router.include_router(cps.router)
api_router.include_router(construction_plan_review.router)
api_router.include_router(agent_callbacks.router)
api_router.include_router(weekly_reports.router)
api_router.include_router(inspection_plans.router)
api_router.include_router(room_checks.router)
