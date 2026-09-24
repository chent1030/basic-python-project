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
    speech,
    tasks,
    weekly_reports,
)
from app.api.v1.endpoints import (
    datasources as ds,
)
from app.projects.room_checks.api import router as room_checks_b6_router_module
from app.skill.coverage.api import coverage_router
from app.skill.effect_evaluation.api import effect_router
from app.skill.memory.api import memory_router
from app.skill.procedural_memory.api import procedural_router

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
api_router.include_router(room_checks_b6_router_module)
api_router.include_router(speech.router)
api_router.include_router(memory_router)
api_router.include_router(coverage_router)
api_router.include_router(procedural_router)
api_router.include_router(effect_router)