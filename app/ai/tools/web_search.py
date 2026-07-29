"""全局工具示例:网络搜索。

每个全局工具一个独立文件(app/ai/tools/<name>.py),避免单文件越来越大。
工具多了就拆更多文件,import 时自动注册(见 __init__.discover_global_tools)。

这是一个最小可用示例:实际可接 Tavily/Serper/Bing 等。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("web_search")
async def web_search(query: str) -> str:
    """搜索互联网并返回与查询相关的结果摘要。

    Args:
        query: 搜索关键词。
    """
    # 占位实现:真实环境接 Tavily/Serper/Bing 等。
    # 用 http_client(已注入)可访问 app.services.http_client.http_client。
    return f"[web_search 占位] 没有真实搜索后端,关键词: {query}"


@tool("save_note")
async def save_note(content: str) -> str:
    """保存一条笔记到记忆中,供后续使用。

    Args:
        content: 要保存的笔记内容。
    """
    # 占位实现:真实环境写文件/DB/向量库。
    return f"[save_note 占位] 已保存({len(content)} 字符)"
