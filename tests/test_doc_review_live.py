from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review.catalog import RULES
from app.projects.doc_review.service import run_doc_review
from app.services.http_client import http_client
from app.services.llm import llm


@pytest.mark.skipif(
    os.getenv("RUN_DOC_REVIEW_LIVE") != "1",
    reason="设置 RUN_DOC_REVIEW_LIVE=1 后才访问真实文件、MinerU和LLM",
)
async def test_live_doc_review() -> None:
    data_path = Path(os.getenv("DOC_REVIEW_DATA", "data.json"))
    initiation_raw = os.getenv("DOC_REVIEW_INITIATION_TIME")
    entity = EhsConstruct.model_validate_json(data_path.read_text(encoding="utf-8"))
    initiation_time = datetime.fromisoformat(initiation_raw) if initiation_raw else None

    await http_client.startup()
    await llm.startup()
    try:
        report = await run_doc_review(entity, initiation_time=initiation_time)
    finally:
        await llm.shutdown()
        await http_client.shutdown()

    assert report["status"] == "completed"
    expected_ids = {rule.rule_id for rule in RULES}
    actual_ids = [result["rule_id"] for result in report["results"]]
    assert len(actual_ids) == len(expected_ids) == 35
    assert len(actual_ids) == len(set(actual_ids))
    assert set(actual_ids) == expected_ids
    assert report["total_files"] > 0
    assert report["decision"] in {"PASSED", "REJECTED", "MANUAL_REVIEW"}
    if os.getenv("DOC_REVIEW_LIVE_STRICT") == "1":
        assert report["warnings"] == []
        unverifiable = [
            result["rule_id"] for result in report["results"] if result["status"] == "UNVERIFIABLE"
        ]
        assert unverifiable == []
