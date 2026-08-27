"""Application service for the typed document-review workflow."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging_config import get_logger
from app.harness.communication.blackboard_var import use_blackboard
from app.harness.context import AgentRunContext
from app.harness.tools.vision import vision_check
from app.models.ehs_contruct_item import EhsConstruct
from app.projects.doc_review.agents import (
    HotWorkFactsAgent,
    ProgrammeFactsAgent,
    TechDisclosureFactsAgent,
    TypoFactsAgent,
    parse_model_output,
)
from app.projects.doc_review.documents import MineruCoordinator, resolve_documents
from app.projects.doc_review.reference import load_reference_data
from app.projects.doc_review.rules import RuleEngine
from app.projects.doc_review.schemas import (
    DocumentType,
    HotWorkFacts,
    ProgrammeFacts,
    SealVisualFact,
    SignatureVisualFact,
    TechDisclosureFacts,
    TypoReviewFacts,
    VisualReviewFacts,
)

log = get_logger("app.projects.doc_review")

OCR_KEYS = {
    DocumentType.CONSTRUCTION_PROGRAMME: "ocr_programme",
    DocumentType.CONSTRUCTION_TECH_DISCLOSE: "ocr_tech_disclose",
    DocumentType.HOT_WORK_DISCLOSE: "ocr_hotwork",
}


async def run_doc_review(
    entity: EhsConstruct | dict[str, Any],
    *,
    initiation_time: datetime | None = None,
) -> dict[str, Any]:
    """Run OCR, parallel fact extraction, visual inspection and rule decisions."""
    model = entity if isinstance(entity, EhsConstruct) else EhsConstruct.model_validate(entity)
    raw_entity = model.model_dump(mode="json")
    documents = resolve_documents(raw_entity)
    context = AgentRunContext(agent_name="doc_review", source="api")
    coordinator = MineruCoordinator(context.blackboard)
    artifacts = await coordinator.process_all(documents)
    warnings: list[str] = []

    grouped: dict[DocumentType, list[str]] = {kind: [] for kind in OCR_KEYS}
    for artifact in artifacts.values():
        if artifact.error:
            warnings.append(f"{artifact.file_name}: OCR失败: {artifact.error}")
        elif artifact.markdown:
            grouped[artifact.document_type].append(
                f"## 文件: {artifact.file_name}\n{artifact.markdown}"
            )
    for document_type, key in OCR_KEYS.items():
        markdown = "\n\n".join(grouped[document_type])
        context.blackboard.write(key, markdown or "[无该文档]", writer="service")
    context.blackboard.write(
        "biz_data",
        json.dumps(_minimal_business_data(model), ensure_ascii=False),
        writer="service",
    )

    programme_task = _extract_if_present(
        ProgrammeFactsAgent,
        ProgrammeFacts,
        grouped[DocumentType.CONSTRUCTION_PROGRAMME],
        context,
    )
    tech_task = _extract_if_present(
        TechDisclosureFactsAgent,
        TechDisclosureFacts,
        grouped[DocumentType.CONSTRUCTION_TECH_DISCLOSE],
        context,
    )
    hot_task = _extract_if_present(
        HotWorkFactsAgent,
        HotWorkFacts,
        grouped[DocumentType.HOT_WORK_DISCLOSE],
        context,
    )
    extracted = await asyncio.gather(
        programme_task,
        tech_task,
        hot_task,
        return_exceptions=True,
    )
    programme = _take_result(extracted[0], ProgrammeFacts, "施工方案提取", warnings)
    tech = _take_result(extracted[1], TechDisclosureFacts, "安全交底提取", warnings)
    hot_work = _take_result(extracted[2], HotWorkFacts, "动火交底提取", warnings)
    context.blackboard.write(
        "biz_data",
        json.dumps(_minimal_business_data(model, programme), ensure_ascii=False),
        writer="service",
    )
    try:
        typo_value = await _run_typed_agent(TypoFactsAgent, TypoReviewFacts, context)
    except Exception as exc:  # noqa: BLE001
        typo_value = exc
    typos = _take_result(typo_value, TypoReviewFacts, "错别字检查", warnings)

    visual = await _collect_visual_facts(context, grouped, warnings)
    engine = RuleEngine(load_reference_data())
    report = engine.evaluate(
        entity=raw_entity,
        documents=documents,
        programme=programme,
        tech=tech,
        hot_work=hot_work,
        typos=typos,
        visual=visual,
        initiation_time=initiation_time or model.processInitiatedAt,
    )
    report.warnings.extend(warnings)
    return report.model_dump(mode="json")


async def _extract_if_present(
    agent_cls,
    schema_model,
    markdown_parts: list[str],
    context: AgentRunContext,
):
    if not markdown_parts:
        return None
    return await _run_typed_agent(agent_cls, schema_model, context)


async def _run_typed_agent(agent_cls, schema_model, parent: AgentRunContext):
    agent = agent_cls()
    child = AgentRunContext(
        agent_name=agent.name,
        messages=[],
        source="internal",
        parent_run_id=parent.run_id,
        depth=parent.depth + 1,
        blackboard=parent.blackboard,
        message_bus=parent.message_bus,
        event_bus=parent.event_bus,
        logger=parent.logger,
    )
    result = await asyncio.wait_for(
        agent.run("从共享黑板提取事实。", context=child),
        timeout=settings.doc_review.check_timeout,
    )
    return parse_model_output(result.output, schema_model)


def _take_result(value, expected_type, label: str, warnings: list[str]):
    if value is None:
        return None
    if isinstance(value, Exception):
        warnings.append(f"{label}失败: {value}")
        return None
    if not isinstance(value, expected_type):
        warnings.append(f"{label}返回类型错误: {type(value).__name__}")
        return None
    return value


async def _collect_visual_facts(
    context: AgentRunContext,
    grouped: dict[DocumentType, list[str]],
    warnings: list[str],
) -> VisualReviewFacts:
    visual = VisualReviewFacts()

    async def collect(document_type: DocumentType, image_key: str, question: str) -> None:
        try:
            raw = await asyncio.wait_for(
                vision_check(image_key, question),
                timeout=settings.doc_review.check_timeout,
            )
            _merge_visual(raw, document_type, visual, warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{document_type.value}视觉检查失败: {exc}")

    with use_blackboard(context.blackboard):
        if grouped[DocumentType.CONSTRUCTION_PROGRAMME]:
            await collect(
                DocumentType.CONSTRUCTION_PROGRAMME,
                "ocr_img:construction_programme",
                "检查施工方案图片并只输出JSON。格式:"
                '{"seal":{"available":true,"has_red_seal":true,'
                '"seal_text":"","in_bottom":true,"confidence":0.0},'
                '"signatures":[{"role":"工程师","handwritten":true,'
                '"date_handwritten":true,"name":"","date":"","confidence":0.0},'
                '{"role":"经理","handwritten":true,"date_handwritten":true,'
                '"name":"","date":"","confidence":0.0},'
                '{"role":"校对人","handwritten":null,"date_handwritten":null,'
                '"name":"","date":"","confidence":0.0},'
                '{"role":"批复人","handwritten":null,"date_handwritten":null,'
                '"name":"","date":"","confidence":0.0}]}。'
                "必须分别判断工程师、经理、校对人和批复人；"
                "无法判断使用null和confidence=0。",
            )
        if grouped[DocumentType.CONSTRUCTION_TECH_DISCLOSE]:
            await collect(
                DocumentType.CONSTRUCTION_TECH_DISCLOSE,
                "ocr_img:construction_tech_disclose",
                "检查安全技术交底图片的全部交底人和被交底人签名日期，只输出JSON:"
                '{"signatures":[{"role":"交底人或被交底人中的实际角色",'
                '"handwritten":true,"date_handwritten":true,"name":"",'
                '"date":"YYYY-MM-DD","confidence":0.0}]}。无法判断使用null。',
            )
        if grouped[DocumentType.HOT_WORK_DISCLOSE]:
            await collect(
                DocumentType.HOT_WORK_DISCLOSE,
                "ocr_img:hot_work_disclose",
                "检查动火交底图片的全部交底人和被交底人签名日期，只输出JSON:"
                '{"signatures":[{"role":"交底人或被交底人中的实际角色",'
                '"handwritten":true,"date_handwritten":true,"name":"",'
                '"date":"YYYY-MM-DD","confidence":0.0}]}。无法判断使用null。',
            )
    return visual


def _merge_visual(
    raw: str,
    document_type: DocumentType,
    visual: VisualReviewFacts,
    warnings: list[str],
) -> None:
    try:
        value = _parse_json_object(raw)
        if value.get("vision_error"):
            raise ValueError(str(value["vision_error"]))
        if document_type == DocumentType.CONSTRUCTION_PROGRAMME and isinstance(
            value.get("seal"), dict
        ):
            visual.seal = SealVisualFact.model_validate(value["seal"])
        signatures = value.get("signatures") or []
        for signature in signatures:
            if isinstance(signature, dict):
                visual.signatures.append(
                    SignatureVisualFact.model_validate(
                        {
                            **signature,
                            "document_id": document_type.value,
                        }
                    )
                )
    except Exception as exc:
        warnings.append(f"{document_type.value}视觉结果解析失败: {exc}")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError("没有有效JSON对象")


def _minimal_business_data(
    entity: EhsConstruct,
    programme: ProgrammeFacts | None = None,
) -> dict[str, Any]:
    person_names = [item.operatorName for item in entity.operator]
    person_names.extend(
        [
            entity.projectManager.projectManagerName,
            entity.guardian.guardianName,
            entity.receptionInfo.receiverName,
            entity.receptionInfo.receiverDirector or "",
            entity.receptionInfo.receptionPersonnelDirectSuperiorName,
            entity.receptionInfo.receptionPersonnelManagerName,
        ]
    )
    if programme is not None:
        person_names.extend(item.name for item in programme.personnel)
        person_names.extend(item.name for item in programme.emergency_roles)
        person_names.extend([programme.proofreader_name, programme.approver_name])
    person_names = list(dict.fromkeys(name.strip() for name in person_names if name.strip()))
    return {
        "vendor_name": entity.vendorName,
        "work_day": entity.workDay,
        "work_types": [item.workType for item in entity.workInfo],
        "work_dates": [item.workDate for item in entity.workInfo],
        "person_names": person_names,
        "process_initiated_at": (
            entity.processInitiatedAt.isoformat() if entity.processInitiatedAt else None
        ),
    }


__all__ = ["run_doc_review"]
