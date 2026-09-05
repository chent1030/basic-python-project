"""Input-file resolution, preview downloading, and visual-asset creation."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Awaitable, Callable, Mapping
from pathlib import PurePath
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.services.http_client import http_client

from .models import VisualAsset

MAX_FILE_BYTES = 50 * 1024 * 1024


class PreviewFile(BaseModel):
    file_id: str
    file_name: str
    preview_url: str
    file_size: int = Field(default=0, ge=0)


class AssetProcessingError(RuntimeError):
    """Raised when a preview cannot be converted into visual assets."""


def _entity_dict(entity: Any) -> Mapping[str, Any]:
    if isinstance(entity, Mapping):
        return entity
    if hasattr(entity, "model_dump"):
        return entity.model_dump(by_alias=True)
    raise TypeError("审核输入必须是字典或 Pydantic 模型")


def resolve_preview_files(entity: Any) -> list[PreviewFile]:
    """Resolve only construction-programme ``s3PreviewFileUrl`` entries."""

    data = _entity_dict(entity)
    values = (
        data.get("constructionProgrammeFileInfoList")
        or data.get("constructionProgrammeFileInfolist")
        or []
    )
    if not isinstance(values, list):
        raise ValueError("constructionProgrammeFileInfoList 必须是数组")

    files: list[PreviewFile] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        if not isinstance(item, Mapping) or item.get("isDelete") is True:
            continue
        url = str(item.get("s3PreviewFileUrl") or "").strip()
        if not url:
            continue
        identity = str(item.get("fileKey") or item.get("id") or index)
        if identity in seen:
            continue
        seen.add(identity)
        files.append(
            PreviewFile(
                file_id=identity,
                file_name=str(item.get("fileName") or f"construction-{index}"),
                preview_url=url,
                file_size=int(item.get("fileSize") or 0),
            )
        )
    return files


async def download_preview(file: PreviewFile) -> bytes:
    """Download one preview URL; no open-url fallback is intentionally used."""

    parsed = urlparse(file.preview_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AssetProcessingError(f"不支持的 s3PreviewFileUrl: {file.preview_url}")
    if file.file_size > MAX_FILE_BYTES:
        raise AssetProcessingError(f"文件超过大小限制: {file.file_size} bytes")
    response = await http_client.get(file.preview_url)
    response.raise_for_status()
    content = response.raw.content
    if not content:
        raise AssetProcessingError("文件内容为空")
    if len(content) > MAX_FILE_BYTES:
        raise AssetProcessingError(f"下载内容超过 {MAX_FILE_BYTES} bytes")
    return content


def _image_asset(
    content: bytes,
    *,
    file: PreviewFile,
    index: int,
    mime_type: str = "image/png",
) -> VisualAsset:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            width, height = image.size
    except Exception as exc:  # PIL exposes multiple format-specific errors.
        raise AssetProcessingError(f"图片无法解码: {file.file_name}") from exc
    digest = hashlib.sha256(content).hexdigest()
    return VisualAsset(
        asset_id=f"{digest[:16]}-{index:04d}",
        document_id=file.file_id,
        file_name=file.file_name,
        asset_index=index,
        mime_type=mime_type,
        image_bytes=content,
        sha256=digest,
        width=width,
        height=height,
    )


def render_visual_assets(content: bytes, file: PreviewFile) -> list[VisualAsset]:
    """Convert an image or PDF into ordered image assets.

    The generated index is an internal asset order, never a document page number.
    PDF support is optional at runtime so deployments that only receive images do
    not need a PDF native dependency.
    """

    suffix = PurePath(file.file_name.lower()).suffix
    if suffix != ".pdf" and not content.startswith(b"%PDF"):
        return [_image_asset(content, file=file, index=0)]

    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AssetProcessingError("PDF 处理需要安装 PyMuPDF") from exc

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise AssetProcessingError(f"PDF 无法打开: {file.file_name}") from exc

    assets: list[VisualAsset] = []
    try:
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            page_bytes = pixmap.tobytes("png")
            assets.append(_image_asset(page_bytes, file=file, index=index))
    finally:
        document.close()
    if not assets:
        raise AssetProcessingError("PDF 没有可处理的页面")
    return assets


Downloader = Callable[[PreviewFile], Awaitable[bytes]]


async def build_assets(
    entity: Any,
    *,
    downloader: Downloader = download_preview,
) -> list[VisualAsset]:
    """Download and render all active construction-programme previews."""

    assets: list[VisualAsset] = []
    for file in resolve_preview_files(entity):
        content = await downloader(file)
        assets.extend(render_visual_assets(content, file))
    return assets


__all__ = [
    "AssetProcessingError",
    "MAX_FILE_BYTES",
    "PreviewFile",
    "build_assets",
    "download_preview",
    "render_visual_assets",
    "resolve_preview_files",
]
