"""记忆 API 入口（FastAPI router）。"""
from __future__ import annotations

from app.skill.memory.api.router import router as memory_router

__all__ = ["memory_router"]