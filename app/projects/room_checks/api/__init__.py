"""B6 视觉点检 Agent API 路由（PRD §23 / §30.1）。

端点：
- POST /api/v1/agent/room-checks/v2/judge
- POST /api/v1/agent/room-checks/v2/rejudge

详见 :mod:`app.projects.room_checks.api.router`。
"""
from __future__ import annotations

from app.projects.room_checks.api.router import router

__all__ = ["router"]