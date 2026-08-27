#!/usr/bin/env python3
"""Verify file download and serialized MinerU OCR without calling an LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import settings
from app.harness.context import AgentRunContext
from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review.documents import MineruCoordinator, resolve_documents
from app.services.http_client import http_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data.json", help="EhsConstruct JSON文件")
    parser.add_argument(
        "--output-dir",
        default="doc-review-ocr-output",
        help="OCR Markdown诊断输出目录；内容可能包含业务敏感信息",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not settings.mineru.url:
        raise RuntimeError("config/local.yaml 中未配置 mineru.url")

    data_path = Path(args.data).resolve()
    entity = EhsConstruct.model_validate_json(data_path.read_text(encoding="utf-8"))
    documents = resolve_documents(entity.model_dump(mode="json"))
    if not documents:
        raise RuntimeError("请求数据中没有可审核文件")

    context = AgentRunContext(agent_name="doc_review_ocr_test", source="script")
    await http_client.startup()
    try:
        artifacts = await MineruCoordinator(context.blackboard).process_all(documents)
    finally:
        await http_client.shutdown()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    summary: list[dict[str, object]] = []
    for index, document in enumerate(documents, start=1):
        artifact = artifacts[document.document_id]
        markdown_path = output_dir / f"{index:02d}-{document.document_type.value}.md"
        if artifact.markdown:
            markdown_path.write_text(artifact.markdown, encoding="utf-8")
        if artifact.error or not artifact.markdown:
            failures.append(document.document_id)
        summary.append(
            {
                "document_id": document.document_id,
                "document_type": document.document_type.value,
                "file_name": document.file_name,
                "markdown_chars": len(artifact.markdown),
                "image_keys": artifact.image_keys,
                "error": artifact.error,
                "markdown_output": str(markdown_path) if artifact.markdown else None,
            }
        )

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"summary": str(summary_path), "documents": summary}, ensure_ascii=False, indent=2
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
