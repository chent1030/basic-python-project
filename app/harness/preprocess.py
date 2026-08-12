"""文件预处理 —— 下载文件 + 类型判断 + 图片分割放大。

独立成层(在 OCR 之前执行):
1. 下载文件(http_client)
2. 判断类型(image/docx/pdf)
3. 图片:长图切4份 + 放大8倍;小图也放大(Pillow)
   docx/pdf:暂不预处理,直接交给 OCR

放大倍数和分割份数在 config 的 doc_review 段可配(默认 8 倍、4 份)。
"""
from __future__ import annotations

import io

from app.core.logging_config import get_logger

log = get_logger("app.harness.preprocess")

# 图片处理默认参数
DEFAULTEnlargeRatio = 8          # 放大倍数
DEFAULT_SPLIT_COUNT = 4          # 长图分割份数
LONG_IMAGE_THRESHOLD = 2000      # 高度超过此值视为长图(像素)


async def download_file(url: str, *, timeout: float = 120) -> bytes:
    """用 http_client 下载文件,返回字节。"""
    from app.services.http_client import http_client

    resp = await http_client.get(url, timeout=timeout)
    raw = resp.raw  # httpx.Response
    content = getattr(raw, "content", None)
    if content is None and hasattr(raw, "aread"):
        content = await raw.aread()
    return content or b""


def detect_file_type(file_bytes: bytes, file_url: str = "") -> str:
    """判断文件类型:image / docx / pdf。

    优先用 magic bytes(文件头),兜底用 URL 扩展名。
    """
    # magic bytes
    if file_bytes[:4] == b"\x89PNG" or file_bytes[:3] == b"\xff\xd8\xff":
        return "image"
    if file_bytes[:5] == b"%PDF-":
        return "pdf"
    if file_bytes[:2] == b"PK":  # docx/xlsx/zip
        if ".docx" in file_url.lower():
            return "docx"
        return "docx"

    # 兜底:URL 扩展名
    url_lower = file_url.lower()
    if any(url_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")):
        return "image"
    if url_lower.endswith(".pdf"):
        return "pdf"
    if url_lower.endswith(".docx"):
        return "docx"
    return "unknown"


def split_and_enlarge(
    image_bytes: bytes,
    *,
    enlarge_ratio: int = DEFAULTEnlargeRatio,
    split_count: int = DEFAULT_SPLIT_COUNT,
    long_threshold: int = LONG_IMAGE_THRESHOLD,
) -> list[bytes]:
    """图片分割 + 放大(Pillow)。

    - 长图(高度 > long_threshold):按 split_count 份纵向切,每份放大 enlarge_ratio 倍
    - 普通图片:直接放大 enlarge_ratio 倍
    返回处理后的图片字节列表(PNG 格式)。

    在线程池里跑(Pillow 是同步的),由调用方包 to_thread。
    """
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    width, height = img.size

    results: list[bytes] = []

    if height > long_threshold:
        # 长图:纵向切 split_count 份
        log.info("长图 %dx%d,切 %d 份,各放大 %d 倍", width, height, split_count, enlarge_ratio)
        part_height = height // split_count
        for i in range(split_count):
            top = i * part_height
            bottom = (i + 1) * part_height if i < split_count - 1 else height
            part = img.crop((0, top, width, bottom))
            enlarged = part.resize(
                (width * enlarge_ratio, (bottom - top) * enlarge_ratio),
                Image.LANCZOS,
            )
            buf = io.BytesIO()
            enlarged.save(buf, format="PNG")
            results.append(buf.getvalue())
    else:
        # 普通图片:直接放大
        log.info("图片 %dx%d,放大 %d 倍", width, height, enlarge_ratio)
        enlarged = img.resize(
            (width * enlarge_ratio, height * enlarge_ratio),
            Image.LANCZOS,
        )
        buf = io.BytesIO()
        enlarged.save(buf, format="PNG")
        results.append(buf.getvalue())

    return results


async def preprocess_file(ctx) -> None:
    """预处理:下载 + 类型判断 + 图片分割放大。直接修改 ctx。

    在 ctx 上设置:file_type / raw_file / processed_images。
    """
    import asyncio

    from app.core.config import settings

    cfg = settings.doc_review
    log.info("[%s] 预处理:下载 %s", ctx.pipeline_id, ctx.file_url)

    # 1. 下载
    ctx.raw_file = await download_file(ctx.file_url, timeout=cfg.check_timeout)
    log.info("[%s] 下载完成 %d 字节", ctx.pipeline_id, len(ctx.raw_file))

    # 2. 类型判断
    ctx.file_type = detect_file_type(ctx.raw_file, ctx.file_url)
    log.info("[%s] 文件类型: %s", ctx.pipeline_id, ctx.file_type)

    # 3. 图片:分割+放大(在线程池跑,Pillow 是同步的)
    if ctx.file_type == "image":
        ctx.processed_images = await asyncio.to_thread(
            split_and_enlarge, ctx.raw_file,
            enlarge_ratio=cfg.enlarge_ratio,
            split_count=cfg.split_count,
            long_threshold=cfg.long_image_threshold,
        )
        log.info(
            "[%s] 图片处理完成: %d 张(分割+放大)",
            ctx.pipeline_id, len(ctx.processed_images),
        )


__all__ = [
    "download_file",
    "detect_file_type",
    "split_and_enlarge",
    "preprocess_file",
]
