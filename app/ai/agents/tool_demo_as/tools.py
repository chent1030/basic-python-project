"""tool_demo_as 的专属工具。

agentscope 版的工具演示:calc + weather,与 tool_demo_da 完全一致。
证明同一套工具定义在 agentscope 后端下也能自动挂载与调用。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("calc")
async def calc(expression: str) -> str:
    """计算数学表达式。

    Args:
        expression: 数学表达式,如 "12 * 8"。
    """
    return f"[calc 占位] {expression} = ?"


@tool("weather")
async def weather(city: str) -> str:
    """查询某城市天气。

    Args:
        city: 城市名。
    """
    return f"[weather 占位] {city}: 晴, 25°C"
