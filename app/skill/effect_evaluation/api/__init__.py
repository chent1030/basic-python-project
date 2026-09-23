"""Effect evaluation API 入口（FastAPI router）。"""
from __future__ import annotations

from app.skill.effect_evaluation.api.router import router as effect_router

__all__ = ["effect_router"]
