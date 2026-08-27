from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import settings
from app.harness.communication import Blackboard
from app.harness.communication.blackboard_var import use_blackboard
from app.harness.tools.extraction import extract_work_checks
from app.harness.tools.mineru_ocr import (
    FilePayload,
    _stash_sub_images_blackboard,
    mineru_ocr,
)
from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review import service
from app.projects.doc_review.documents import resolve_documents
from app.projects.doc_review.reference import load_reference_data
from app.projects.doc_review.schemas import DocumentType, ProgrammeFacts, VisualReviewFacts
from tests.doc_review_fixtures import sample_entity_data


async def test_work_check_extraction_distinguishes_all_four_states() -> None:
    result = json.loads(
        await extract_work_checks(
            "☐高处作业 ×吊装作业 √动火作业 临时用电作业",
            "高处作业,吊装作业,动火作业,临时用电作业",
        )
    )

    assert result == {
        "checked": ["动火作业"],
        "unchecked": ["吊装作业"],
        "blank": ["高处作业"],
        "unknown": ["临时用电作业"],
    }


async def test_work_check_extraction_supports_marks_after_labels() -> None:
    result = json.loads(
        await extract_work_checks(
            "高处作业☐ 吊装作业× 动火作业√",
            "高处作业,吊装作业,动火作业",
        )
    )
    assert result["checked"] == ["动火作业"]
    assert result["unchecked"] == ["吊装作业"]
    assert result["blank"] == ["高处作业"]


async def test_ambiguous_mark_between_labels_remains_unknown() -> None:
    result = json.loads(
        await extract_work_checks(
            "高处作业 ☐ 吊装作业",
            "高处作业,吊装作业",
        )
    )
    assert result == {
        "checked": [],
        "unchecked": [],
        "blank": [],
        "unknown": ["高处作业", "吊装作业"],
    }


def test_hot_work_alias_survives_request_validation() -> None:
    raw = sample_entity_data()
    raw["hotWorkDiscloseFileInfoList"] = raw["constructionTechDiscloseFileInfoList"]
    model = EhsConstruct.model_validate(raw)
    documents = resolve_documents(model.model_dump(mode="json"))

    assert len(model.hotWorkTechDiscloseFileInfoList) == 1
    assert any(item.document_type == DocumentType.HOT_WORK_DISCLOSE for item in documents)


def test_aggregated_images_preserve_multiple_documents_and_deduplicate() -> None:
    board = Blackboard()
    with use_blackboard(board):
        _stash_sub_images_blackboard(
            "construction_programme",
            [{"name": "first.jpg", "data": "data:first"}],
        )
        _stash_sub_images_blackboard(
            "construction_programme",
            [
                {"name": "first.jpg", "data": "data:duplicate"},
                {"name": "second.jpg", "data": "data:second"},
            ],
        )

    assert board.read("ocr_img:construction_programme") == [
        {"name": "first.jpg", "data": "data:first"},
        {"name": "second.jpg", "data": "data:second"},
    ]


def test_visual_error_is_reported_as_warning() -> None:
    warnings: list[str] = []
    service._merge_visual(
        '{"vision_error":"model unavailable"}',
        DocumentType.CONSTRUCTION_PROGRAMME,
        VisualReviewFacts(),
        warnings,
    )

    assert warnings
    assert "model unavailable" in warnings[0]


def test_default_rule_reference_path_is_independent_of_working_directory(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    reference = load_reference_data()
    assert reference.ppe_requirements == {}


async def test_agent_execution_uses_configured_timeout(monkeypatch) -> None:
    class SlowAgent:
        name = "slow"

        async def run(self, message, *, context):
            await asyncio.sleep(1)

    monkeypatch.setattr(settings.doc_review, "check_timeout", 0.001)
    with pytest.raises(TimeoutError):
        await service._run_typed_agent(SlowAgent, ProgrammeFacts, service.AgentRunContext())


async def test_mineru_checks_http_status_before_parsing(monkeypatch) -> None:
    class FailedResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("503 service unavailable")

        def json(self):
            raise AssertionError("error response must not be parsed as JSON")

    async def failed_post(*args, **kwargs):
        return FailedResponse()

    monkeypatch.setattr(settings.mineru, "url", "https://mineru.example.test/ocr")
    monkeypatch.setattr("app.harness.tools.mineru_ocr.http_client.post", failed_post)

    result = await mineru_ocr([FilePayload(name="test.pdf", content=b"pdf")])
    assert result["md"].startswith("[mineru_ocr 错误]")
    assert "503 service unavailable" in result["md"]
