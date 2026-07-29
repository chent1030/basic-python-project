"""tool_demo_da 的专属工具。

deepagents 版的工具演示:calc + weather。与 tool_demo_as 的工具完全一致,
仅因所属 agent 不同(分别由 registry 在加载对应 agent 时 import 注册)。
证明专属工具在两后端下都能自动挂载。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("calc")
async def calc(expression: str) -> str:
    """计算数学表达式。

    Args:
        expression: 数学表达式,如 "12 * 8"。
    """
    # 占位:真实环境用安全方式求值
    return f"[calc 占位] {expression} = ?"


@tool("weather")
async def weather(city: str) -> str:
    """查询某城市天气。

    Args:
        city: 城市名。
    """
    return f"[weather 占位] {city}: 晴, 25°C"
