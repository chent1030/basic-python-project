"""单 agent + 工具循环。最基础拓扑。"""
from __future__ import annotations

from app.harness.base import BaseAgent


class BaseSingleAgent(BaseAgent):
    """单 agent。run() 直接调后端(已由 BaseAgent._execute_topology 默认实现)。"""
    pass
