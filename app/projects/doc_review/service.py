"""文档审核 service —— 一次请求跑一次审核 pipeline。

一次 POST 包含整个 EhsConstruct（含多个文件：施工方案书、安全交底书等），
审核 pipeline 一次处理所有文件，生成一份综合报告。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging_config import get_logger
from app.projects.doc_review.agents import DocReviewFlow

log = get_logger("app.projects.doc_review")


def extract_files(entity: dict) -> list[dict]:
    """从 EhsConstruct dict 提取所有待审核文件。

    返回 [{"name": 文件名, "url": URL, "type": 文件类型}, ...]。
    """
    files: list[dict] = []
    for key in (
        "constructionProgrammeFileInfolist",
        "constructionTechDiscloseFileInfoList",
    ):
        for f in entity.get(key, []):
            if isinstance(f, dict) and f.get("s3PreviewFileUrl"):
                files.append({
                    "name": f.get("fileName", "unknown"),
                    "url": f["s3PreviewFileUrl"],
                    "category": key,  # 标记文件类别
                })
    return files


async def run_doc_review(entity: dict[str, Any]) -> dict[str, Any]:
    """一次审核：把 EhsConstruct 的所有文件 + 业务数据交给 pipeline，跑一次。

    Args:
        entity: EhsConstruct 业务对象（含供应商/人员/多个文件等）。

    Returns:
        综合审核报告。
    """
    files = extract_files(entity)
    log.info("文档审核开始: %d 个文件", len(files))

    if not files:
        return {"status": "failed", "error": "未找到待审核文件"}

    # 把所有文件信息 + 业务数据拼成一次输入，pipeline 一次处理
    files_json = json.dumps(files, ensure_ascii=False)
    entity_json = json.dumps(entity, ensure_ascii=False)
    message = (
        f"## 业务数据（比对基准）\n{entity_json}\n\n"
        f"## 待审核文件列表\n{files_json}\n\n"
        f"请对所有文件进行审核。"
    )

    flow = DocReviewFlow()
    try:
        result = await flow.run(message)
        report = _parse_report(result.output)
        report["status"] = "completed"
        report["total_files"] = len(files)
        return report
    except Exception as e:
        log.exception("审核失败")
        return {"status": "failed", "error": str(e)}


def _parse_report(text: str) -> dict:
    """从 agent 输出解析 JSON 报告。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": text[:500], "overall_pass": None}


__all__ = ["run_doc_review", "extract_files"]
