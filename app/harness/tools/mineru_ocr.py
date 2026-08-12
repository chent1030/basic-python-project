"""MinerU OCR 工具 —— 把文件发给 MinerU，拿 OCR 结果。

数据流:
  preprocess(下载+类型判断+图片分割放大) → mineru_ocr(发处理后的图片给MinerU→拿结果)

返回结构:
  JSON 字符串: {"md": "markdown文本", "images": [{"name": "xxx", "data": "data:image/png;base64,..."}]}
  md: OCR 识别的文档全文(markdown)
  images: MinerU 返回的图片(含公章/表格等),每张含 name + base64 data URI
          后续可用 check_red_seal 工具分析图片是否含红色公章

本工具职责:
  1. 下载文件（由 preprocess 的 download_file 完成）
  2. 如果是图片:先预处理(split_and_enlarge 分割+放大)
  3. 把处理后的文件/图片逐张发给 MinerU
  4. 拼 OCR markdown + 收集图片 → 返回标准 JSON
"""
from __future__ import annotations

import json

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.tools import tool

log = get_logger("app.harness.tools.mineru_ocr")


@tool("mineru_ocr")
async def mineru_ocr(file_url: str) -> str:
    """对指定 URL 的文件做 OCR，返回 JSON。

    返回: {"md": "markdown文本", "images": [{"name": "xxx", "data": "data:image/...;base64,..."}]}
    md 是 OCR 识别的文档全文;images 是 MinerU 返回的图片(含公章等),
    可用 check_red_seal 分析图片是否有红色公章。

    Args:
        file_url: 待识别文件的 URL（支持 PDF/图片/docx）。
    """
    cfg = settings.mineru
    if not cfg.url:
        return json.dumps(
            {"md": "", "images": [], "error": "未配置 MinerU 服务地址"},
            ensure_ascii=False,
        )

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

        # 2. 逐张发给 MinerU,收集 markdown + 图片
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
        md_parts: list[str] = []
        all_images: list[dict] = []

        for fname, fbytes in files_to_send:
            log.info("发 MinerU: %s", fname)
            resp = await http_client.post(
                cfg.url,
                files={"file": (fname, fbytes)},
                headers=headers,
                timeout=cfg.timeout,
            )
            data = resp.json()
            md_parts.append(_extract_markdown(data))
            all_images.extend(_extract_images(data, prefix=fname))

        # 3. 返回标准结构
        result = {
            "md": "\n\n---\n\n".join(md_parts),
            "images": all_images,
        }
        log.info(
            "OCR 完成: md=%d字符 images=%d张", len(result["md"]), len(all_images)
        )
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        log.exception("MinerU OCR 失败: %s", file_url)
        return json.dumps(
            {"md": "", "images": [], "error": str(e)},
            ensure_ascii=False,
        )


def _extract_markdown(data: dict | list) -> str:
    """从 MinerU 返回提取 markdown 文本。"""
    if isinstance(data, dict):
        md = data.get("md") or data.get("markdown") or data.get("content") or ""
        if not md:
            results = data.get("results") or data.get("pages") or []
            if isinstance(results, list):
                md = "\n\n".join(
                    r.get("md", "") or r.get("markdown", "")
                    if isinstance(r, dict) else str(r)
                    for r in results
                )
        return md
    return str(data)[:2000]


def _extract_images(data: dict | list, *, prefix: str = "") -> list[dict]:
    """从 MinerU 返回提取图片。

    返回 [{"name": "page_0_img_0", "data": "data:image/png;base64,..."}]。
    兼容 MinerU 多种返回格式:
    - images: [{"name": ..., "data": ...}] 或 [{"name": ..., "base64": ...}]
    - image_urls: ["http://..."]  (URL 形式,不转 base64)
    """
    images: list[dict] = []
    if not isinstance(data, dict):
        return images

    raw_imgs = data.get("images") or data.get("imgs") or []
    if isinstance(raw_imgs, list):
        for i, img in enumerate(raw_imgs):
            if isinstance(img, dict):
                name = img.get("name") or f"{prefix}_img_{i}"
                # 优先 data(可能已是 data URI 或 base64)
                img_data = img.get("data") or img.get("base64") or ""
                if img_data and not img_data.startswith("data:"):
                    # 纯 base64,补 data URI 前缀
                    mime = img.get("mime") or "image/png"
                    img_data = f"data:{mime};base64,{img_data}"
                if img_data:
                    images.append({"name": name, "data": img_data})
            elif isinstance(img, str) and img.startswith("data:"):
                images.append({"name": f"{prefix}_img_{i}", "data": img})

    # URL 形式的图片(不下载转 base64,保留 URL)
    raw_urls = data.get("image_urls") or []
    if isinstance(raw_urls, list):
        for i, url in enumerate(raw_urls):
            if isinstance(url, str) and url:
                images.append({"name": f"{prefix}_url_{i}", "data": url})

    return images
