"""Coverage API 入口（FastAPI router）。"""
from __future__ import annotations

from app.skill.coverage.api.router import router as coverage_router

__all__ = ["coverage_router"]