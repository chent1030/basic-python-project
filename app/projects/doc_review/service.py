"""文档审核 service —— 编排:下载文件 → 预处理 → 跑 DocReviewFlow → 返回报告。

从 EhsConstruct 提取待审核文件,对每个文件跑 agent pipeline。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging_config import get_logger
from app.projects.doc_review.agents import DocReviewFlow

log = get_logger("app.projects.doc_review")


def extract_files(entity: dict) -> list[tuple[str, str]]:
    """从 EhsConstruct dict 提取待审核文件(文件名, URL)。"""
    files: list[tuple[str, str]] = []
    for key in ("constructionProgrammeFileInfolist", "constructionTechDiscloseFileInfoList"):
        for f in entity.get(key, []):
            if isinstance(f, dict) and f.get("s3PreviewFileUrl"):
                files.append((f.get("fileName", "unknown"), f["s3PreviewFileUrl"]))
    return files


async def run_doc_review(entity: dict[str, Any]) -> dict[str, Any]:
    """对 EhsConstruct 里的每个文件跑审核 pipeline,汇总返回。

    Args:
        entity: EhsConstruct 业务对象(含供应商/人员/文件列表等)。

    Returns:
        汇总审核报告 dict。
    """
    files = extract_files(entity)
    log.info("文档审核开始: %d 个文件", len(files))

    if not files:
        return {"status": "failed", "error": "未找到待审核文件"}

    flow = DocReviewFlow()
    results: list[dict] = []

    for filename, url in files:
        log.info("审核文件: %s (%s)", filename, url)
        # 把 entity 作为比对基准 + url 作为输入,拼成 agent 的输入
        entity_json = json.dumps(entity, ensure_ascii=False)
        message = f"业务数据(比对基准):\n{entity_json}\n\n文件 URL: {url}"

        try:
            result = await flow.run(message)
            report = _parse_report(result.output)
            report["filename"] = filename
            report["file_url"] = url
            results.append(report)
        except Exception as e:
            log.exception("文件 %s 审核失败", filename)
            results.append({
                "filename": filename,
                "file_url": url,
                "status": "failed",
                "error": str(e),
            })

    all_pass = all(
        r.get("overall_pass", False) for r in results if r.get("overall_pass") is not None
    )
    return {
        "status": "completed",
        "overall_pass": all_pass,
        "total_files": len(results),
        "files": results,
    }


def _parse_report(text: str) -> dict:
    """从 agent 输出解析 JSON 报告。"""
    m = __import__("re").search(r"\{.*\}", text, __import__("re").DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": text[:500], "overall_pass": None}


__all__ = ["run_doc_review", "extract_files"]
