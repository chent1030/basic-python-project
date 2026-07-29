"""researcher 的专属工具。

agent 目录下的 tools.py 定义的 @tool 会自动注册并挂到该 agent(见 registry)。
与全局工具(app/ai/tools/)的区别:这些工具只属于本 agent。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("analyze_topic")
async def analyze_topic(topic: str) -> str:
    """对给定主题做结构化分析(占位示例)。

    Args:
        topic: 要分析的主题。
    """
    return f"[analyze_topic 占位] 主题「{topic}」的关键维度:背景、现状、趋势、风险。"
