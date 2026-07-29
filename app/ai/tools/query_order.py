"""全局工具:查订单(客服类 agent 共用)。

从专属工具提升为全局工具 —— support_bot / support_bot_da / support_bot_as 都引用它。
专属工具适合只服务单个 agent 的;多个 agent 共用的放这里(app/ai/tools/)。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("query_order")
async def query_order(order_id: str) -> str:
    """查询订单状态(占位示例)。

    Args:
        order_id: 订单号。
    """
    return f"[query_order 占位] 订单 {order_id} 状态:已发货,预计明日送达。"
