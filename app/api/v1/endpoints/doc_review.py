"""文档智能审核端点 —— Harness Pipeline 模式。

外部系统 POST EhsConstruct(完整业务对象)→ 提取待审核文件
→ 对每个文件跑 Pipeline(预处理→OCR→章节→检查→报告)→ 汇总返回。
同步处理,无需回调。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging_config import get_logger
from app.harness.pipeline import Pipeline
from app.harness.stages import (
    CheckStage,
    ExtractStage,
    OcrStage,
    PreprocessStage,
    ReportStage,
)
from app.models.ehs_contruct_item import EhsConstruct

log = get_logger("app.api.doc_review")

router = APIRouter(prefix="/doc-review", tags=["doc-review"])


def build_doc_review_pipeline() -> Pipeline:
    """组装文档审核 Pipeline:5 个 Stage 顺序执行。"""
    return Pipeline(stages=[
        PreprocessStage(),    # 下载 + 类型判断 + 图片分割放大
        OcrStage(),           # MinerU OCR(发处理后的图片/文件,拿文本+图片)
        ExtractStage(),       # 章节提取
        CheckStage(),         # 并行执行所有自动发现的检查项
        ReportStage(),        # LLM 汇总报告
    ])


def extract_files_from_entity(entity: EhsConstruct) -> list[tuple[str, str]]:
    """从 EhsConstruct 提取待审核文件。

    返回 [(文件名, URL), ...]。施工方案书 + 安全交底书。
    """
    files: list[tuple[str, str]] = []
    for f in entity.constructionProgrammeFileInfolist:
        files.append((f.fileName, f.s3PreviewFileUrl))
    for f in entity.constructionTechDiscloseFileInfoList:
        files.append((f.fileName, f.s3PreviewFileUrl))
    return files


@router.post("")
async def submit_review(body: EhsConstruct) -> dict[str, Any]:
    """提交文档审核(同步)。

    从 EhsConstruct 提取待审核文件(施工方案书、安全交底书等),
    对每个文件跑 Pipeline(预处理→OCR→检查→报告),汇总返回。
    """
    import json

    entity_dict = json.loads(body.model_dump_json())
    files = extract_files_from_entity(body)
    log.info("收到审核请求 %d 个文件", len(files))

    if not files:
        return {"status": "failed", "error": "未找到待审核文件"}

    pipeline = build_doc_review_pipeline()
    results: list[dict] = []

    for filename, url in files:
        log.info("审核文件: %s (%s)", filename, url)
        ctx = await pipeline.run(entity=entity_dict, file_url=url)
        report = ctx.report
        report["filename"] = filename
        report["file_url"] = url
        report["file_type"] = ctx.file_type
        report["checks_detail"] = {
            name: r.to_dict() for name, r in ctx.check_results.items()
        }
        results.append(report)

    # 汇总所有文件
    all_pass = all(r.get("overall_pass", False) for r in results)
    return {
        "status": "completed",
        "overall_pass": all_pass,
        "total_files": len(results),
        "files": results,
    }


@router.post("/single")
async def submit_single(body: dict[str, Any]) -> dict[str, Any]:
    """单文件审核(直接传 entity + url,不走 EhsConstruct)。

    body: {"entity": {...}, "url": "..."}
    """
    entity = body.get("entity", {})
    url = body.get("url", "")
    if not url:
        return {"status": "failed", "error": "缺少 url"}

    pipeline = build_doc_review_pipeline()
    ctx = await pipeline.run(entity=entity, file_url=url)
    report = ctx.report
    report["file_type"] = ctx.file_type
    report["checks_detail"] = {
        name: r.to_dict() for name, r in ctx.check_results.items()
    }
    return report
