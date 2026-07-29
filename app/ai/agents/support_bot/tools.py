"""support_bot 的专属工具。

注:query_order 已提升为全局工具(app/ai/tools/query_order.py),
被多个客服 agent(support_bot/_da/_as)共用,这里不再重复定义。
本文件演示专属工具的写法:这里放只服务 support_bot 的工具。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("escalate_human")
async def escalate_human(reason: str) -> str:
    """升级到人工客服(只服务 support_bot 的专属工具示例)。

    Args:
        reason: 升级原因。
    """
    return f"[escalate_human 占位] 已转人工,原因: {reason}"
