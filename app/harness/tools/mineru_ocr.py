"""MinerU OCR 工具 —— 调 MinerU HTTP 服务做文档 OCR。

将文件 POST 给 MinerU HTTP 服务(地址在 config 的 mineru.url),拿回结构化
markdown(含表格/页面顺序)。返回 dict:

    {
      "md": str,     # OCR 后 markdown。图片引用保留为占位符,绝不含 base64。
    }

关键设计:OCR 出的图片(base64)绝不出现在工具结果里 —— 那样会被 deepagents
整段塞进 LLM 上下文(工具结果 dict→ToolMessage),导致大图撑爆上下文。
图片一律只写到共享黑板 `ocr_img:<文件名>`,需要时再用 fetch_result 按名取回,
再决定是否进上下文(可参考 app/harness/tools/blackboard_io.py 的 store_result/
fetch_result)。

图片预处理:
- 长图(高/宽比超过 mineru.long_image_ratio 默认 3):竖直等分为
  mineru.long_image_segments(默认 4)段,每段 mineru.long_image_scale(默认 7)倍
  放大,批量取 OCR。
- 普通小图:短边低于 mineru.image_target_side(默认 2000)时先放大。
- PDF/非图片:原样提交。

这些预处理开关/阈值都通过 getattr 以默认值读取。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
from dataclasses import dataclass

from PIL import Image

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.tools import tool
from app.services.http_client import http_client

Image.MAX_IMAGE_PIXELS = None

log = get_logger("mineru_ocr")


@dataclass
class FilePayload:
    """一个提交单元：最终 POST 给 MinerU 的单个文件。

    `name` 是该段文件在 MinerU 请求里的文件名（长图分段后为 `<原始名>_partN.ext`），
    `content` 是字节内容。长图分段 / 放大在调用方前置完成，这里只承载内容。
    """

    name: str
    content: bytes


def _stash_images_blackboard(tasks: list[tuple[str, bytes]], images: list[dict[str, str]]) -> None:
    """把 OCR 图片(base64)单独存到共享黑板,不进 LLM 上下文。

    每个提交单元按名写入黑板的 `ocr_img:<文件名>` 键;长图分段后同一原始
    文件会有多个段,这里分别按段名写入。后续步骤若需看图,可用
    fetch_result 按名精确取回。

    工具在 agent 运行上下文中执行,黑板由 _run_member 注入;非运行态下
    没有黑板则静默跳过(顶层调用 mineru_ocr 时)。
    """
    if not images:
        return
    try:
        from app.harness.communication.blackboard_var import get_blackboard

        bb = get_blackboard()
    except Exception:
        return
    for image, (name, _content) in zip(images, tasks, strict=False):
        stem = os.path.splitext(os.path.basename(name))[0]
        bb.write(f"ocr_img:{stem}", [image], writer="mineru_ocr")
        log.info("OCR 图片已入黑板 ocr_img:%s (1 张)", stem)


def _stash_sub_images_blackboard(agg_key: str, images: list[dict[str, str]]) -> None:
    """把 MinerU 抽取的子图聚合写入黑板键 `ocr_img:<agg_key>`(固定键)。

    与 _stash_images_blackboard 不同:这里的图片来自 MinerU 响应的
    `results[*].images`(干净的局部裁剪),聚合成**一个**键,视觉工具
    拿一个固定键即可看到全部子图,无需 LLM 猜测哪个键是封面/盖章图。
    非运行态(无黑板)下静默跳过。
    """
    if not images:
        return
    try:
        from app.harness.communication.blackboard_var import get_blackboard

        bb = get_blackboard()
    except Exception:
        return
    merged = bb.extend_unique(
        f"ocr_img:{agg_key}",
        images,
        identity=lambda item: (
            (item.get("name") or item.get("data")) if isinstance(item, dict) else item
        ),
        writer="mineru_ocr",
    )
    log.info("OCR 子图已入黑板 ocr_img:%s (%d 张)", agg_key, len(merged))


async def prepare_file(files_urls: list[str]) -> list[FilePayload]:
    """前置处理阶段：下载并预处理一批 URL，产出待 OCR 的提交单元数组。

    对每个 URL：下载字节 → 长图分段(若超阈值) → 放大/上采样，得到一个或多个
    FilePayload(长图分段后一个原始文件对应多个提交单元，按段提交)。PDF/其他
    非图片原样返回。`mineru_ocr` 只消费这里产出的数组，不再处理文件本身。

    Args:
        files_urls: 待识别文件的 URL 列表(支持 PDF/图片)。
    """
    cfg = settings.mineru
    payloads: list[FilePayload] = []
    for url in files_urls:
        filename = url.rsplit("=", 1)[-1] or "document"
        try:
            log.info("下载文件: %s", url)
            dl = await http_client.get(url, timeout=cfg.timeout)
            dl.raise_for_status()
            file_content = dl.raw.content
            if not file_content:
                log.warning("下载内容为空: %s", url)
                continue
            tasks = _prepare_ocr_files(file_content, filename, cfg)
            payloads.extend(FilePayload(name=name, content=content) for name, content in tasks)
            log.info("前置: %s 产出 %d 个提交单元", filename, len(tasks))
        except Exception as e:
            log.warning("文件下载/预处理失败，跳过 %s: %s", url, e)
    return payloads


def prepare_bytes(file_content: bytes, filename: str) -> list[FilePayload]:
    """Preprocess already-downloaded content into MinerU submission units."""
    tasks = _prepare_ocr_files(file_content, filename, settings.mineru)
    return [FilePayload(name=name, content=content) for name, content in tasks]


@tool("mineru_ocr")
async def mineru_ocr(files: list[FilePayload], agg_key: str = "") -> dict:
    """对一组已预处理好的文件做 OCR 识别,返回合并后的 markdown。

    文件(长图分段 / 放大)已由调用方前置处理好,这里只负责上传与 OCR。

    Args:
        files: 待提交的 (文件名, 字节) 提交单元列表。长图分段后一个原始文件
            对应多个 submit unit,会一次性批量 POST,OCR 结果按提交顺序合并。
        agg_key: 可选。非空时,把 MinerU 返回的全部子图(base64)聚合成一张
            `ocr_img:<agg_key>` 合集写入黑板,供视觉工具用**固定键**取图。
            典型用途:封面/盖章页(P0-F2 公章比对)用 agg_key="cover_seal"。
            不传则保持原行为,只按提交单元各写一张原始图。

    注意:
        files 只能来自**同一个逻辑文件**(或其长图分段),不能混入多个不同的
        原始文件。实测 MinerU 服务一次 POST 多个不同文件时只识别第一个、
        忽略其余。不同文件之间必须各自单独调用本工具,且一次只发一个请求
        (MinerU 也不支持并发,并发会产生空结果)。
    """
    cfg = settings.mineru
    if not cfg.url:
        return {
            "md": "[mineru_ocr 错误]未配置 MinerU 服务地址,请在 config 的 mineru.url 填入。",
        }
    if not files:
        return {"md": "[mineru_ocr 错误]传入的文件数组为空。"}

    tasks = [(p.name, p.content) for p in files]
    log.info("调 MinerU OCR: %s (提交 %d 个文件)", cfg.url, len(tasks))

    headers: dict[str, str] | None = None
    if cfg.api_key:
        headers = {"Authorization": f"Bearer {cfg.api_key}"}

    # 提交前把处理的图片转成 base64 data URL 附件,便于按名写黑板
    processed_images = _to_image_attachments(tasks)

    try:
        resp = await http_client.post(
            cfg.url,
            files=[("files", (name, content)) for name, content in tasks],
            data={
                "return_content_list": False,
                "return_images": True,
                "ocr": True,
                "enable_extra_text_detection": True,
                "language": "zh",
                "backend": "vlm-transformers",
            },
            headers=headers,
            timeout=cfg.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.exception("MinerU OCR 失败")
        return {"md": f"[mineru_ocr 错误] {e}", "images": []}

    # 提取 OCR 结果,按提交顺序拼接各段 markdown
    md_parts: list[str] = []
    sub_images: list[dict[str, str]] = []
    if isinstance(data, dict):
        results = data.get("results") or {}
        for name, _ in tasks:
            item = results.get(name.split(".")[0]) or {}
            md = item.get("md_content")
            if md:
                md_parts.append(_normalize_md(md))
            imgs = item.get("images")
            if isinstance(imgs, dict):
                # MinerU 返回的抽取子图: {<hash>.jpg: "data:<mime>;base64,..."}
                # 这些才是干净的局部裁剪(盖章/签字/表格区域),按 hash 去重后聚合。
                seen: set[str] = set()
                for k, v in imgs.items():
                    if not isinstance(v, str) or not v:
                        continue
                    if k in seen:
                        continue
                    seen.add(k)
                    sub_images.append({"name": k, "data": v})
    md = "\n\n".join(md_parts) or "[mineru_ocr 错误]OCR 未返回任何内容。"

    # 把 OCR 图片(base64)写入黑板,每个提交单元一个键 ocr_img:<name>
    _stash_images_blackboard(tasks, processed_images)

    # agg_key: 把 MinerU 返回的抽取子图聚合成固定键 ocr_img:<agg_key>,
    # 供视觉工具(如公章比对)用**固定键**取图,避免 LLM 猜黑板键。
    if agg_key:
        _stash_sub_images_blackboard(agg_key, sub_images or processed_images)
    return {"md": md}


def _to_image_attachments(tasks: list[tuple[str, bytes]]) -> list[dict[str, str]]:
    """把预处理后的图片任务转成 base64 data URL 附件,带 mime 类型推断。

    仅对图片类任务生成附件;PDF 等非图片不作为图片附件。
    """
    atts: list[dict[str, str]] = []
    for name, content in tasks:
        try:
            img = Image.open(io.BytesIO(content))
            mime = Image.MIME.get(img.format, "image/jpeg")
        except Exception:
            fn = name.lower()
            if fn.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")):
                ext = fn.rsplit(".", 1)[-1]
                mime = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "bmp": "image/bmp",
                    "webp": "image/webp",
                    "tif": "image/tiff",
                    "tiff": "image/tiff",
                }.get(ext, "application/octet-stream")
            else:
                continue  # PDF/其它,不作为图片附件
        b64 = base64.b64encode(content).decode("ascii")
        atts.append({"name": name, "data": f"data:{mime};base64,{b64}"})
    return atts


def _normalize_md(md: str) -> str:
    """规范 OCR 出的 markdown:图片引用保留为占位符,不内嵌 base64。

    MinerU 返回的 markdown 中图片写作 `![](images/55d04c....jpg)`,
    base64 图在 images 字段(见 `images/` 下附件注释)。这里原样保留
    占位符,避免大图 base64 撑爆 LLM 上下文;需要看图时再按名查 images。
    """
    return md


def _maybe_upscale_image(file_content: bytes, filename: str, cfg) -> bytes:
    """进 OCR 前先判断图片大小,若较短边太小则等比例放大。

    返回放大后的图片字节(仍为原始图片格式);不是图片(如 PDF)原样返回。
    """
    if not getattr(cfg, "image_upscale_enabled", True):
        return file_content

    try:
        img = Image.open(io.BytesIO(file_content))
    except Exception:
        return file_content  # 不是可识别的图片,原样传给 OCR(如 PDF)

    width, height = img.size
    min_side = min(width, height)
    target = getattr(cfg, "image_target_side", 2000)
    if min_side >= target or width <= 0 or height <= 0:
        return file_content  # 已经够大,无需放大

    scale = target / min_side
    new_width = round(width * scale)
    new_height = round(height * scale)

    fmt = img.format or "JPEG"
    try:
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format=fmt)
    except Exception:
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.convert("RGB").save(buf, format="PNG")

    log.info(
        "图片放大: %s %dx%d -> %dx%d (短边%d->%d)",
        filename,
        width,
        height,
        new_width,
        new_height,
        min_side,
        target,
    )
    return buf.getvalue()


def _prepare_ocr_files(file_content: bytes, filename: str, cfg) -> list[tuple[str, bytes]]:
    """进 OCR 前的预处理,返回一组 (文件名, 文件字节) 待提交任务。

    - 长图(高/宽比超过阈值):竖直等分为 N 段,每段放大,批量提交。
    - 普通小图:走 _maybe_upscale_image 放大。
    - PDF/非图片:原样提交。
    """
    if getattr(cfg, "long_image_strip", True):
        segs = _split_long_image(file_content, filename, cfg)
        if segs:
            return segs
    return [(filename, _maybe_upscale_image(file_content, filename, cfg))]


def _split_long_image(file_content: bytes, filename: str, cfg) -> list[tuple[str, bytes]] | None:
    """长图竖直等分 + 每段放大。非长图/非图片返回 None。"""
    try:
        img = Image.open(io.BytesIO(file_content))
    except Exception:
        return None  # 非图片,交给后续普通流程

    width, height = img.size
    ratio = getattr(cfg, "long_image_ratio", 3.0)
    if height <= ratio * width:
        return None  # 不是长图

    segments = getattr(cfg, "long_image_segments", 4)
    scale = getattr(cfg, "long_image_scale", 7.0)
    fmt = img.format or "JPEG"

    base, ext = os.path.splitext(filename)
    ext = ext or ".jpg"

    tasks: list[tuple[str, bytes]] = []
    seg_h = height / segments
    for i in range(segments):
        top = round(i * seg_h)
        bottom = height if i == segments - 1 else round((i + 1) * seg_h)
        seg = img.crop((0, top, width, bottom))
        new_w = round(seg.width * scale)
        new_h = round(seg.height * scale)
        seg = seg.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        try:
            seg.save(buf, format=fmt)
        except Exception:
            seg.convert("RGB").save(buf, format="PNG")
        seg_name = f"{base}_part{i + 1}{ext}"
        tasks.append((seg_name, buf.getvalue()))
        log.info(
            "长图分段: %s 第%s段 %dx%d -> %dx%d (%g倍)",
            filename,
            i + 1,
            seg.width,
            seg.height,
            new_w,
            new_h,
            scale,
        )

    return tasks


async def main():
    await http_client.startup()  # 关键:先初始化
    try:
        files = [FilePayload(name="document.jpg", content=b"")]
        res = await mineru_ocr(files)
        print(res.get("md", res))
        print("\n[注意:图片已写入黑板 ocr_img:<文件名>,不在返回结果里]")
    finally:
        await http_client.shutdown()  # 用完记得关,避免连接泄漏


if __name__ == "__main__":
    asyncio.run(main())
