#!/usr/bin/env python3
"""Run one real document review against configured file, MinerU and LLM services."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review.catalog import RULES
from app.projects.doc_review.documents import resolve_documents
from app.projects.doc_review.reference import load_reference_data
from app.projects.doc_review.rules import normalize_work_type
from app.projects.doc_review.service import run_doc_review
from app.services.http_client import http_client
from app.services.llm import llm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data.json", help="EhsConstruct JSON文件")
    parser.add_argument(
        "--initiation-time",
        help="流程发起时间，ISO-8601格式，例如2026-08-10T09:30:00",
    )
    parser.add_argument(
        "--output",
        default="doc-review-report.json",
        help="审核报告输出路径",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="警告、不可验证规则或规则集合不完整时返回非零状态",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not settings.mineru.url:
        raise RuntimeError("config/local.yaml 中未配置 mineru.url")
    if not settings.llm.providers:
        raise RuntimeError("config/local.yaml 中未配置 llm.providers")

    data_path = Path(args.data).resolve()
    entity = EhsConstruct.model_validate_json(data_path.read_text(encoding="utf-8"))
    documents = resolve_documents(entity.model_dump(mode="json"))
    if not documents:
        raise RuntimeError("请求数据中没有可审核的施工方案/安全交底/动火交底文件")
    reference = load_reference_data()
    entity_work_types = {
        normalized for item in entity.workInfo if (normalized := normalize_work_type(item.workType))
    }
    missing_ppe = sorted(
        work_type
        for work_type in entity_work_types
        if not reference.ppe_requirements.get(work_type)
    )
    if args.strict and missing_ppe:
        raise RuntimeError("strict模式缺少作业类型PPE基准:" + ",".join(missing_ppe))
    if args.strict and "动火作业" in entity_work_types:
        if not reference.hot_work_ppe:
            raise RuntimeError("strict模式缺少hot_work_ppe")
        if not reference.hot_work_risk_template.strip():
            raise RuntimeError("strict模式缺少hot_work_risk_template")
    initiation_time = datetime.fromisoformat(args.initiation_time) if args.initiation_time else None

    await http_client.startup()
    await llm.startup()
    try:
        report = await run_doc_review(entity, initiation_time=initiation_time)
    finally:
        await llm.shutdown()
        await http_client.shutdown()

    output_path = Path(args.output).resolve()
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    expected_ids = {rule.rule_id for rule in RULES}
    actual_ids = [item["rule_id"] for item in report["results"]]
    duplicate_ids = sorted({rule_id for rule_id in actual_ids if actual_ids.count(rule_id) > 1})
    missing_ids = sorted(expected_ids - set(actual_ids))
    extra_ids = sorted(set(actual_ids) - expected_ids)
    unverifiable_ids = [
        item["rule_id"] for item in report["results"] if item["status"] == "UNVERIFIABLE"
    ]
    diagnostics = {
        "expected_rule_count": len(expected_ids),
        "actual_rule_count": len(actual_ids),
        "missing_rule_ids": missing_ids,
        "extra_rule_ids": extra_ids,
        "duplicate_rule_ids": duplicate_ids,
        "unverifiable_rule_ids": unverifiable_ids,
        "warnings": report["warnings"],
    }
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "summary": report["summary"],
                "output": str(output_path),
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    invariant_errors = (
        missing_ids or extra_ids or duplicate_ids or len(actual_ids) != len(expected_ids)
    )
    if invariant_errors:
        return 2
    if args.strict and (report["warnings"] or unverifiable_ids):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
