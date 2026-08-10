"""Aggregates all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import agents, auth, chat, doc_review, examples, items, tasks
from app.api.v1.endpoints import datasources as ds

api_router = APIRouter()
api_router.include_router(items.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(ds.router)
api_router.include_router(examples.router)
api_router.include_router(tasks.router)
api_router.include_router(agents.router)
api_router.include_router(doc_review.router)
