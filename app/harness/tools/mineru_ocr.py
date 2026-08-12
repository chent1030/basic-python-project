"""MinerU OCR 工具 —— 把文件发给 MinerU，拿 OCR 结果。

数据流:
  preprocess(下载+类型判断+图片分割放大) → mineru_ocr(发处理后的图片给MinerU→拿markdown)

本工具职责:
  1. 下载文件（由 preprocess 的 download_file 完成）
  2. 如果是图片:先预处理(split_and_enlarge 分割+放大)
  3. 把处理后的文件/图片逐张发给 MinerU
  4. 拼 OCR markdown 结果 + 返回图片
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.tools import tool

log = get_logger("app.harness.tools.mineru_ocr")


@tool("mineru_ocr")
async def mineru_ocr(file_url: str) -> str:
    """对指定 URL 的文件做 OCR，返回结构化文档（markdown）。

    流程: 下载 → 判断类型 → 图片预处理(分割+放大) → 发给 MinerU → 拼 markdown。
    如果是图片会自动分割放大(长图切4份×放大8倍),小图也放大。

    Args:
        file_url: 待识别文件的 URL（支持 PDF/图片/docx）。
    """
    cfg = settings.mineru
    if not cfg.url:
        return "[mineru_ocr 错误]未配置 MinerU 服务地址。"

    try:
        import asyncio

        from app.harness.preprocess import detect_file_type, download_file, split_and_enlarge
        from app.services.http_client import http_client

        # 1. 下载 + 判断类型 + 预处理（图片分割放大）
        file_bytes = await download_file(file_url, timeout=cfg.timeout)
        file_type = detect_file_type(file_bytes, file_url)
        log.info("文件 %s 类型=%s %d字节", file_url, file_type, len(file_bytes))

        if file_type == "image":
            processed = await asyncio.to_thread(split_and_enlarge, file_bytes)
            files_to_send = [
                (f"page_{i}.png", img) for i, img in enumerate(processed)
            ]
            log.info("图片预处理: %d 张", len(files_to_send))
        else:
            filename = file_url.rsplit("/", 1)[-1] or "document"
            files_to_send = [(filename, file_bytes)]

        # 2. 逐张发给 MinerU
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
        parts: list[str] = []
        ocr_images: list[str] = []

        for fname, fbytes in files_to_send:
            log.info("发 MinerU: %s", fname)
            resp = await http_client.post(
                cfg.url,
                files={"file": (fname, fbytes)},
                headers=headers,
                timeout=cfg.timeout,
            )
            data = resp.json()
            parts.append(_extract_markdown(data))
            ocr_images.extend(_extract_images(data))

        # 3. 拼接结果
        result = "\n\n---\n\n".join(parts)
        if ocr_images:
            result += f"\n\n[OCR图片]: {ocr_images}"
        log.info("OCR 完成: %d字符 %d图片", len(result), len(ocr_images))
        return result

    except Exception as e:
        log.exception("MinerU OCR 失败: %s", file_url)
        return f"[mineru_ocr 错误] {e}"


def _extract_markdown(data: dict | list) -> str:
    """从 MinerU 返回提取 markdown。"""
    if isinstance(data, dict):
        md = data.get("markdown") or data.get("content") or data.get("text", "")
        if not md:
            results = data.get("results") or data.get("pages") or []
            if isinstance(results, list):
                md = "\n\n".join(
                    r.get("markdown", "") if isinstance(r, dict) else str(r)
                    for r in results
                )
        return md
    return str(data)[:2000]


def _extract_images(data: dict | list) -> list[str]:
    """从 MinerU 返回提取图片。"""
    if isinstance(data, dict):
        imgs = data.get("images") or data.get("image_urls") or []
        if isinstance(imgs, list):
            return [str(i) for i in imgs if i]
    return []
