"""MinerU OCR 工具 —— 调 MinerU HTTP 服务做文档 OCR。

MinerU 部署为独立 HTTP 服务（地址在 config 的 mineru.url）。
下载文件 → POST 给 MinerU → 返回 OCR 结果（markdown）+ 图片。
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.tools import tool

log = get_logger("app.harness.tools.mineru_ocr")


@tool("mineru_ocr")
async def mineru_ocr(file_url: str) -> str:
    """对指定 URL 的文件做 OCR 识别，返回结构化文档（markdown，含表格和页面顺序）。

    Args:
        file_url: 待识别文件的 URL 地址（支持 PDF/图片）。
    """
    cfg = settings.mineru
    if not cfg.url:
        return "[mineru_ocr 错误]未配置 MinerU 服务地址，请在 config 的 mineru.url 填入。"

    try:
        from app.services.http_client import http_client

        # 1. 下载文件
        log.info("下载文件: %s", file_url)
        dl = await http_client.get(file_url, timeout=cfg.timeout)
        raw = dl.raw
        content = getattr(raw, "content", None)
        if content is None and hasattr(raw, "aread"):
            content = await raw.aread()

        # 2. POST 给 MinerU
        headers = {}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        filename = file_url.rsplit("/", 1)[-1] or "document"
        log.info("调 MinerU OCR: %s (%d 字节)", cfg.url, len(content or b""))
        resp = await http_client.post(
            cfg.url,
            files={"file": (filename, content)},
            headers=headers or None,
            timeout=cfg.timeout,
        )
        data = resp.json()

        # 3. 提取结果
        if isinstance(data, dict):
            md = data.get("markdown") or data.get("content") or data.get("text", "")
            if not md:
                results = data.get("results") or data.get("pages") or []
                if isinstance(results, list):
                    md = "\n\n".join(
                        r.get("markdown", "") if isinstance(r, dict) else str(r)
                        for r in results
                    )
            if md:
                return md
            return f"[mineru_ocr] OCR 完成但返回为空: {str(data)[:500]}"
        return str(data)[:2000]

    except Exception as e:
        log.exception("MinerU OCR 失败: %s", file_url)
        return f"[mineru_ocr 错误] {e}"
