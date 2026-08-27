from __future__ import annotations

import json

from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review import service
from app.projects.doc_review.catalog import RULES
from app.projects.doc_review.documents import resolve_documents
from app.projects.doc_review.reference import RuleReferenceData
from app.projects.doc_review.schemas import (
    DocumentType,
    OcrArtifact,
    ProgrammeFacts,
    TechDisclosureFacts,
    TypoReviewFacts,
    VisualReviewFacts,
)
from tests.doc_review_fixtures import sample_entity_data


def _sample_entity() -> EhsConstruct:
    return EhsConstruct.model_validate(sample_entity_data())


def test_resolve_documents_only_selects_review_documents() -> None:
    entity = _sample_entity()
    documents = resolve_documents(entity.model_dump(mode="json"))

    assert [item.document_type for item in documents] == [
        DocumentType.CONSTRUCTION_PROGRAMME,
        DocumentType.CONSTRUCTION_TECH_DISCLOSE,
    ]
    assert len(documents) == 2


def test_programme_file_legacy_alias_survives_request_validation() -> None:
    raw = _sample_entity().model_dump(mode="json")
    raw["constructionProgrammeFileInfolist"] = raw.pop("constructionProgrammeFileInfoList")
    entity = EhsConstruct.model_validate(raw)

    assert len(entity.constructionProgrammeFileInfoList) == 1


async def test_service_orchestration_can_run_with_mocked_external_services(monkeypatch) -> None:
    entity = _sample_entity()

    async def fake_process_all(self, documents):
        return {
            item.document_id: OcrArtifact(
                document_id=item.document_id,
                document_type=item.document_type,
                file_name=item.file_name,
                markdown=f"# mocked {item.document_type.value}",
            )
            for item in documents
        }

    async def fake_typed_agent(agent_cls, schema_model, parent):
        if schema_model is ProgrammeFacts:
            return ProgrammeFacts()
        if schema_model is TechDisclosureFacts:
            return TechDisclosureFacts()
        if schema_model is TypoReviewFacts:
            return TypoReviewFacts(
                by_document={
                    DocumentType.CONSTRUCTION_PROGRAMME.value: [],
                    DocumentType.CONSTRUCTION_TECH_DISCLOSE.value: [],
                }
            )
        return schema_model()

    async def fake_visual(context, grouped, warnings):
        return VisualReviewFacts()

    monkeypatch.setattr(service.MineruCoordinator, "process_all", fake_process_all)
    monkeypatch.setattr(service, "_run_typed_agent", fake_typed_agent)
    monkeypatch.setattr(service, "_collect_visual_facts", fake_visual)
    monkeypatch.setattr(service, "load_reference_data", lambda: RuleReferenceData())

    report = await service.run_doc_review(entity)

    assert report["status"] == "completed"
    assert report["total_files"] == 2
    assert len(report["results"]) == len(RULES)
    assert len({item["rule_id"] for item in report["results"]}) == len(RULES)
    assert report["decision"] in {"REJECTED", "MANUAL_REVIEW"}
    json.dumps(report, ensure_ascii=False)
