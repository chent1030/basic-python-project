"""Procedural API 入口（FastAPI router）。"""
from __future__ import annotations

from app.skill.procedural_memory.api.router import router as procedural_router

__all__ = ["procedural_router"]
