"""黑板读写工具 —— agent 间的「指定数据获取传递」。

这些 @tool 供 agent 的 LLM 调用,借助 ContextVar 访问当前共享黑板:

    store_result(key, value)  # 把某段数据写到黑板(如 OCR 结果)
    fetch_result(key)         # 精确取回在 ocr **生成传递** 时写入的黑板数据

用于替代把整坨合并消息/base64 图片塞进 LLM 上下文的方式 —— agent 按 key
精确取自己想要的那份数据,实现"指定数据获取/传值"。
"""

from __future__ import annotations

import json

from app.harness.communication.blackboard_var import get_blackboard
from app.harness.tools import tool


@tool("store_result")
async def store_result(key: str, value: str) -> str:
    """把一段数据写入共享黑板,供其它 agent 指定获取。

    Args:
        key: 黑板键(如 ocr_programme)。
        value: 要传递的数据内容(如 OCR 后的文档文本)。
    """
    get_blackboard().write(key, value)
    return f"ok,已写入黑板 key={key}"


@tool("fetch_result")
async def fetch_result(key: str) -> str:
    """从共享黑板精确取回指定 key 的数据(指定目标 agent 的输出)。

    Args:
        key: 黑板键(如 ocr_programme —— 即绑定到某个 OCR agent 的输出)。
    """
    bb = get_blackboard()
    value = bb.read(key)
    if value is None:
        return f"[未找到 key={key},当前黑板键: {bb.keys()}]"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


__all__ = ["store_result", "fetch_result"]
