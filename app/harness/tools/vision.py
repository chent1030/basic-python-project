"""视觉服务 gateway —— 通过多模态 LLM 判定图片内容（红章 / 手写签字）。

需求里凡是需要「真正看图」的检查（红色公章、手写签名、人像等），OCR 文本
无法可靠覆盖，需要一个独立的视觉服务：把图片字节喂给支持图文的 OpenAI 兼容
模型，让它输出结构化判断结果。

设计要点：
- 复用 `llm.providers` 里的**图文模型**（@tool 不新增 config 段），默认用
  `default_provider`（项目里即 `uabc`，模型 Qwen3.5-122B-A10B-241，支持图）。
- 图片来源：黑板 `ocr_img:<文件名>` 键（mineru_ocr 写入，存的是
  `[{"name":..., "data": "data:<mime>;base64,..."}]`）。agent 运行时才拿得到黑板。
- 通讯：LangChain `HumanMessage` 的 content 用多模态块列表
  `[{"type":"text",...},{"type":"image_url","image_url":{"url":...}}]`，
  直接传 BaseMessage 列表给 `llm.invoke`（不经过 str 强转）。
- 工具返回文本，供 agent 落地为 JSON 结论。多图分次调用（一次一个 key）。
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from app.core.logging_config import get_logger
from app.harness.tools import tool

log = get_logger("vision")


def _read_blackboard_image(bb_key: str) -> list[dict[str, str]] | None:
    """从黑板 `ocr_img:<文件名>` 读图片列表；取不到返回 None。

    MinerU 写入黑板的值形如:[{"name": 文件名, "data": "data:mime;base64,xxx"}]。
    """
    from app.harness.communication.blackboard_var import get_blackboard

    try:
        bb = get_blackboard()
        value = bb.read(bb_key)
    except Exception:  # 非运行态(无黑板/未注入)
        return None
    if not value:
        return None
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value
    if isinstance(value, dict):
        # 兼容单张图被存成 dict 的情形
        return [value]
    return None


def _img_to_message_block(item: dict[str, str]) -> dict[str, str] | None:
    """把黑板图条目转成 LangChain 多模态图块；非图片跳过。

    item 结构:{name, data}；data 为 data URL(带 base64)。若 data 不是
    base64(例如落盘路径),尝试转成 data URL。
    """
    data = item.get("data") or ""
    if not data:
        return None
    if not data.startswith("data:"):
        # 不是 data URL 则按裸 base64 包装
        data = f"data:image/jpeg;base64,{data}"
    return {"type": "image_url", "image_url": {"url": data}}


@tool("vision_check")
async def vision_check(image_key: str, question: str, provider: str = "") -> str:
    """对指定的 OCR 图片做一次视觉判定,返回模型的结构化 JSON 文本。

    从黑板 key=`image_key`(形如 ocr_img:<文件名>)取回图片,连同 question 一起
    发给多模态模型(默认 default_provider=uabc),让模型看图回答。

    Args:
        image_key: 黑板里的图片键,形如 ocr_img:<文件名>。
        question:  要模型回答的问题(如"图片中是否有红色公章?""有无手写签名?")。
        provider:  复用哪个 llm.providers(默认用 default_provider,uabc 图文模型)。

    返回:
        JSON 字符串,由 agent 进一步汇总。调用失败返回
        {"vision_error": "<原因>"},上层可判为不可用。
    """
    from app.services.llm import llm

    images = _read_blackboard_image(image_key)
    if not images:
        return json.dumps(
            {"vision_error": f"黑板键 {image_key} 无可用图片"},
            ensure_ascii=False,
        )

    content: list[dict[str, object]] = [{"type": "text", "text": question}]
    added = 0
    for item in images:
        block = _img_to_message_block(item)
        if block:
            content.append(block)
            added += 1
    if not added:
        return json.dumps(
            {"vision_error": f"黑板键 {image_key} 的图片无法解码"},
            ensure_ascii=False,
        )

    msg = HumanMessage(content=content)
    try:
        text = await llm.invoke(
            [msg],
            provider=provider or None,
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("视觉服务调用失败: %s", image_key)
        return json.dumps({"vision_error": str(e)}, ensure_ascii=False)

    return text.strip()


@tool("vision_check_seal")
async def vision_check_seal(image_key: str) -> str:
    """视觉判定指定图片是否含红色公章（红章）。"""
    q = (
        "请检查这张图片中的红色公章。回答 JSON："
        '{"has_red_seal": true/false, "seal_text": "公章上的文字",'
        ' "seal_color": "红色|其他颜色", "in_bottom": true/false,'
        ' "confidence": 0~1}。无法判断则 confidence 为 0。'
    )
    raw = await vision_check(image_key, q)
    if raw.startswith('{"vision_error"'):
        return raw
    return raw


@tool("vision_check_sign")
async def vision_check_sign(image_key: str) -> str:
    """视觉判定指定图片是否含手写签字及其日期。"""
    q = (
        "请检查这张图片中的签名和日期。回答 JSON："
        '{"has_handwritten": true/false, "names": ["识别出的签名人名"],'
        ' "date": "识别的日期(如4.5)", "has_date": true/false,'
        ' "confidence": 0~1}。无法判断则 confidence 为 0。'
    )
    raw = await vision_check(image_key, q)
    if raw.startswith('{"vision_error"'):
        return raw
    return raw


__all__ = ["vision_check", "vision_check_seal", "vision_check_sign"]
