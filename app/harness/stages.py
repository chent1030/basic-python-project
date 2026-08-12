"""文档审核的 5 个 Stage:预处理 → OCR → 章节提取 → 并行检查 → 汇总报告。

每个 Stage 继承 BaseStage,读写 HarnessContext。
CheckStage 并行执行所有自动发现的检查项。
ReportStage 用 LLM 汇总。
"""
from __future__ import annotations

import asyncio
import json

from app.core.logging_config import get_logger
from app.harness.base import BaseStage, extract_sections
from app.harness.context import HarnessContext
from app.harness.preprocess import preprocess_file

log = get_logger("app.harness.stages")


class PreprocessStage(BaseStage):
    """下载文件 + 类型判断 + 图片分割放大。"""

    name = "preprocess"

    async def run(self, ctx: HarnessContext) -> HarnessContext:
        await preprocess_file(ctx)
        log.info(
            "[%s] 预处理完成 type=%s images=%d",
            ctx.pipeline_id, ctx.file_type, len(ctx.processed_images),
        )
        return ctx


class OcrStage(BaseStage):
    """调 MinerU OCR。发处理后的图片/文件,拿文本 + 图片。

    图片:逐张发 processed_images 给 MinerU,拼接结果。
    docx/pdf:发 raw_file。
    MinerU 服务地址在 config.mineru.url。
    """

    name = "ocr"

    async def run(self, ctx: HarnessContext) -> HarnessContext:
        from app.core.config import settings

        cfg = settings.mineru
        if not cfg.url:
            ctx.ocr_text = "[OCR 错误]未配置 MinerU 服务地址"
            log.warning("[%s] MinerU 未配置,跳过 OCR", ctx.pipeline_id)
            return ctx

        from app.services.http_client import http_client

        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None

        try:
            if ctx.file_type == "image" and ctx.processed_images:
                # 图片:逐张发(分割放大后的),拼接结果
                parts: list[str] = []
                for i, img_bytes in enumerate(ctx.processed_images):
                    log.info(
                        "[%s] OCR 图片 %d/%d",
                        ctx.pipeline_id, i + 1, len(ctx.processed_images),
                    )
                    resp = await http_client.post(
                        cfg.url,
                        files={"file": (f"page_{i}.png", img_bytes)},
                        headers=headers,
                        timeout=cfg.timeout,
                    )
                    data = resp.json()
                    md = _extract_markdown(data)
                    parts.append(md)
                    # MinerU 可能返回处理后的图片
                    ctx.ocr_images.extend(_extract_images(data))
                ctx.ocr_text = "\n\n---\n\n".join(parts)
            else:
                # docx/pdf:发原始文件
                filename = ctx.file_url.rsplit("/", 1)[-1] or "document"
                resp = await http_client.post(
                    cfg.url,
                    files={"file": (filename, ctx.raw_file)},
                    headers=headers,
                    timeout=cfg.timeout,
                )
                data = resp.json()
                ctx.ocr_text = _extract_markdown(data)
                ctx.ocr_images = _extract_images(data)

            log.info("[%s] OCR 完成 %d 字符, %d 图片",
                     ctx.pipeline_id, len(ctx.ocr_text), len(ctx.ocr_images))
        except Exception as e:
            log.exception("[%s] OCR 失败", ctx.pipeline_id)
            ctx.ocr_text = f"[OCR 错误] {e}"

        return ctx


class ExtractStage(BaseStage):
    """章节提取:把 OCR markdown 拆成 sections。"""

    name = "extract"

    async def run(self, ctx: HarnessContext) -> HarnessContext:
        ctx.sections = extract_sections(ctx.ocr_text)
        log.info("[%s] 章节提取: %s", ctx.pipeline_id, list(ctx.sections))
        return ctx


class CheckStage(BaseStage):
    """并行执行所有自动发现的检查项。"""

    name = "checks"

    async def run(self, ctx: HarnessContext) -> HarnessContext:
        from app.harness.registry import discover_checks

        checks = discover_checks()
        log.info("[%s] 并行检查 %d 项", ctx.pipeline_id, len(checks))

        async def _run_one(name, check):
            try:
                return name, await check.run(ctx)
            except Exception as e:
                log.exception("[%s] 检查项 '%s' 失败", ctx.pipeline_id, name)
                from app.harness.context import CheckResult

                return name, CheckResult(
                    name=name, passed=False, severity="high",
                    issues=[f"检查异常: {e}"],
                )

        results = await asyncio.gather(
            *[_run_one(n, c) for n, c in checks.items()]
        )
        ctx.check_results = {name: result for name, result in results}
        log.info("[%s] 检查完成", ctx.pipeline_id)
        return ctx


class ReportStage(BaseStage):
    """汇总所有检查结果,用 LLM 生成报告。"""

    name = "report"

    async def run(self, ctx: HarnessContext) -> HarnessContext:
        from app.services.llm import llm

        all_results = "\n\n".join(
            f"### {name}\n{result.to_dict()}"
            for name, result in ctx.check_results.items()
        )
        entity_text = json.dumps(ctx.entity, ensure_ascii=False)

        prompt = (
            "你是文档审核报告生成专家。把各项检查结果整合成报告。\n"
            f"业务数据:{entity_text}\n\n"
            f"各项检查结果:\n{all_results}\n\n"
            "生成 JSON 报告:\n"
            '{"overall_pass": true/false, "summary": "共检查N项,X项不通过",\n'
            ' "checks": [{"name","pass","severity","issues","suggestion"}],\n'
            ' "overall_suggestion": "..."}'
        )
        try:
            text = await llm.invoke([{"role": "user", "content": prompt}])
            ctx.report = _parse_report(text)
        except Exception as e:
            log.exception("[%s] 汇总报告失败", ctx.pipeline_id)
            ctx.report = {"overall_pass": False, "error": str(e)}

        # 补充元信息
        ctx.report.setdefault("checks", [])
        ctx.report["pipeline_id"] = ctx.pipeline_id
        return ctx


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _extract_markdown(data: dict | list) -> str:
    """从 MinerU 返回里提取 markdown 文本。"""
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
    """从 MinerU 返回里提取图片(URL 或 base64)。"""
    images: list[str] = []
    if isinstance(data, dict):
        imgs = data.get("images") or data.get("image_urls") or []
        if isinstance(imgs, list):
            images = [str(i) for i in imgs if i]
    return images


def _parse_report(text: str) -> dict:
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": text, "overall_pass": None}


__all__ = [
    "PreprocessStage",
    "OcrStage",
    "ExtractStage",
    "CheckStage",
    "ReportStage",
]
