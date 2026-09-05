"""DeepAgents-backed orchestration for construction-programme review."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from app.models.ehs_contruct_item import EhsConstruct

from .assets import (
    AssetProcessingError,
    Downloader,
    download_preview,
    render_visual_assets,
    resolve_preview_files,
)
from .models import (
    ConstructionFacts,
    ExtractionResult,
    FindingSeverity,
    FindingStatus,
    ProcessingStats,
    ReviewConfig,
    ReviewReport,
    ReviewStatus,
    RuleFinding,
    VisualAsset,
)
from .providers import ProviderError, ProviderRegistry
from .rules import ConstructionPlanRuleEngine
from .visual_checks import RedSealColorVerifier


class ArtifactStore:
    """Small run-local artifact store; replace with a durable backend in production."""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def put(self, prefix: str, value: object) -> str:
        artifact_id = f"{prefix}-{uuid.uuid4().hex}"
        self._values[artifact_id] = value
        return artifact_id

    def get(self, artifact_id: str) -> object:
        return self._values[artifact_id]


class ConstructionPlanReviewOrchestrator:
    """Run the new construction-plan-only workflow.

    Downloading and image extraction are deterministic tasks. DeepAgents is used
    for the document-fact synthesis step; deterministic rules then evaluate the
    resulting typed facts so business decisions are reproducible.
    """

    def __init__(
        self,
        *,
        providers: ProviderRegistry | None = None,
        rule_engine: ConstructionPlanRuleEngine | None = None,
        color_verifier: RedSealColorVerifier | None = None,
        downloader: Downloader = download_preview,
    ) -> None:
        self.providers = providers or ProviderRegistry()
        self.rule_engine = rule_engine or ConstructionPlanRuleEngine()
        self.color_verifier = color_verifier or RedSealColorVerifier()
        self.downloader = downloader
        self.artifacts = ArtifactStore()

    async def run(
        self,
        entity: EhsConstruct | Mapping[str, Any],
        *,
        config: ReviewConfig | None = None,
    ) -> ReviewReport:
        review_config = config or ReviewConfig()
        run_id = f"run-{uuid.uuid4().hex}"
        raw_entity = self._entity_dict(entity)
        retries = 0

        try:
            assets, asset_retries = await self._prepare_assets(raw_entity, review_config)
            retries += asset_retries
        except Exception as exc:
            return self._unavailable(run_id, str(exc), file_count=0)
        if not assets:
            return self._unavailable(run_id, "没有可审核的施工方案文件", file_count=0)

        observations, extraction_retries = await self._extract_assets(assets, review_config)
        retries += extraction_retries
        failed_assets = sum(1 for item in observations if item.error)
        valid_observations = [item for item in observations if not item.error]
        if not valid_observations:
            return self._unavailable(
                run_id,
                "所有视觉资产识别失败",
                file_count=len({asset.document_id for asset in assets}),
                asset_count=len(assets),
                failed_asset_count=failed_assets,
                retry_count=retries,
            )

        self.artifacts.put("observations", observations)
        try:
            facts, synthesis_retries = await self._assemble_with_retry(
                raw_entity,
                assets,
                valid_observations,
                review_config,
            )
            retries += synthesis_retries
        except ProviderError as exc:
            return self._unavailable(
                run_id,
                str(exc),
                file_count=len({asset.document_id for asset in assets}),
                asset_count=len(assets),
                failed_asset_count=failed_assets,
                retry_count=retries,
            )
        facts = self._enforce_color_verdict(facts, assets, valid_observations)
        self.artifacts.put("facts", facts)
        findings = self.rule_engine.evaluate(entity=raw_entity, facts=facts)
        findings = self._mark_unavailable_findings(findings, failed_assets)
        status = self._status(findings)
        summary = {
            "reject_count": sum(
                item.severity == FindingSeverity.REJECT and item.status == FindingStatus.FAIL
                for item in findings
            ),
            "suggestion_count": sum(
                item.severity == FindingSeverity.SUGGESTION and item.status == FindingStatus.FAIL
                for item in findings
            ),
            "reminder_count": sum(
                item.severity == FindingSeverity.REMINDER and item.status == FindingStatus.FAIL
                for item in findings
            ),
            "review_required_count": sum(
                item.status == FindingStatus.REVIEW_REQUIRED for item in findings
            ),
        }
        return ReviewReport(
            run_id=run_id,
            status=status,
            summary=summary,
            findings=findings,
            processing=ProcessingStats(
                file_count=len({asset.document_id for asset in assets}),
                asset_count=len(assets),
                failed_asset_count=failed_assets,
                retry_count=retries,
            ),
            created_at=datetime.now(UTC),
        )

    async def _assemble_with_retry(
        self,
        entity: Mapping[str, Any],
        assets: list[VisualAsset],
        observations: list[ExtractionResult],
        config: ReviewConfig,
    ) -> tuple[ConstructionFacts, int]:
        """Retry only the structured-fact synthesis when it fails transiently."""

        last_error: ProviderError | None = None
        for attempt in range(config.max_retries + 1):
            try:
                return (
                    await self._assemble_facts(entity, assets, observations, config),
                    attempt,
                )
            except ProviderError as exc:
                last_error = exc
                if attempt < config.max_retries:
                    await asyncio.sleep(config.retry_base_delay * (2**attempt))
        raise last_error or ProviderError("ConstructionFacts 汇总失败")

    async def _prepare_assets(
        self,
        entity: Mapping[str, Any],
        config: ReviewConfig,
    ) -> tuple[list[VisualAsset], int]:
        files = resolve_preview_files(entity)
        semaphore = asyncio.Semaphore(config.max_concurrency)
        retries = 0

        async def prepare(file: Any) -> list[VisualAsset]:
            nonlocal retries
            async with semaphore:
                last_error: Exception | None = None
                for attempt in range(config.max_retries + 1):
                    try:
                        content = await self.downloader(file)
                        return render_visual_assets(content, file)
                    except Exception as exc:  # download/render providers expose varied exceptions
                        last_error = exc
                        if attempt < config.max_retries:
                            retries += 1
                            await asyncio.sleep(config.retry_base_delay * (2**attempt))
                raise AssetProcessingError(str(last_error or "文件处理失败"))

        batches = await asyncio.gather(*(prepare(file) for file in files), return_exceptions=True)
        assets: list[VisualAsset] = []
        errors: list[str] = []
        for batch in batches:
            if isinstance(batch, Exception):
                errors.append(str(batch))
            else:
                assets.extend(batch)
        if not assets and errors:
            raise AssetProcessingError("；".join(errors))
        return assets, retries

    async def _extract_assets(
        self,
        assets: list[VisualAsset],
        config: ReviewConfig,
    ) -> tuple[list[ExtractionResult], int]:
        semaphore = asyncio.Semaphore(config.max_concurrency)
        retry_count = 0

        async def extract(asset: VisualAsset) -> ExtractionResult:
            nonlocal retry_count
            async with semaphore:
                provider_names = [config.provider]
                if config.fallback_provider and config.fallback_provider != config.provider:
                    provider_names.append(config.fallback_provider)
                last_error = ""
                for provider_index, provider_name in enumerate(provider_names):
                    try:
                        provider = self.providers.get(provider_name)
                    except ProviderError as exc:
                        last_error = str(exc)
                        continue
                    attempts = config.max_retries + 1 if provider_index == 0 else 1
                    for attempt in range(1, attempts + 1):
                        try:
                            result = await provider.extract(
                                asset,
                                model=config.model,
                                attempt=attempt,
                            )
                            if self._needs_confidence_retry(result):
                                raise ProviderError("视觉识别置信度不足")
                            return result
                        except Exception as exc:  # noqa: BLE001 - provider boundary
                            last_error = str(exc)
                            if attempt < attempts:
                                retry_count += 1
                                await asyncio.sleep(config.retry_base_delay * (2 ** (attempt - 1)))
                return ExtractionResult(
                    asset_id=asset.asset_id,
                    provider=config.provider,
                    model=config.model,
                    error=last_error or "视觉识别失败",
                )

        results = await asyncio.gather(*(extract(asset) for asset in assets))
        return list(results), retry_count

    @staticmethod
    def _needs_confidence_retry(result: ExtractionResult) -> bool:
        """Retry empty or uniformly low-confidence visual extractions."""

        if not result.elements:
            return True
        return max(item.confidence for item in result.elements) < 0.65

    async def _assemble_facts(
        self,
        entity: Mapping[str, Any],
        assets: list[VisualAsset],
        observations: list[ExtractionResult],
        config: ReviewConfig,
    ) -> ConstructionFacts:
        """Ask a DeepAgents synthesis agent to normalize observations into facts."""

        enriched: list[dict[str, Any]] = []
        assets_by_id = {asset.asset_id: asset for asset in assets}
        for observation in observations:
            asset = assets_by_id[observation.asset_id]
            seal_candidates = [
                item for item in observation.elements if item.type == "seal_candidate"
            ]
            candidate_box = seal_candidates[0].bbox if seal_candidates else None
            color = self.color_verifier.verify(asset.image_bytes, candidate_box)
            enriched.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_index": asset.asset_index,
                    "elements": [item.model_dump() for item in observation.elements],
                    "raw_text": observation.raw_text,
                    "seal_color_check": color.model_dump(),
                }
            )
        prompt = (
            "你是施工方案事实汇总 Agent。根据业务上下文和视觉识别结果，只返回符合"
            "ConstructionFacts 结构的 JSON，不要输出解释。只审核施工方案，不处理技术交底"
            "和动火交底。不要判断公章是否位于底部。公章 is_red 必须优先使用"
            "seal_color_check 的图像分析结果；seal_text 仅从视觉元素提取。"
            f"业务上下文：{json.dumps(dict(entity), ensure_ascii=False, default=str)}\n"
            f"视觉识别结果：{json.dumps(enriched, ensure_ascii=False)}"
        )
        synthesis_provider = observations[0].provider or config.provider
        try:
            from deepagents import create_deep_agent
            from langchain_core.messages import HumanMessage

            from app.services.llm import llm

            agent = create_deep_agent(
                model=llm._get_model(synthesis_provider),
                system_prompt="你只负责把视觉观察归一化为严格 JSON 事实对象。",
                response_format=ConstructionFacts,
                name="construction-facts-synthesizer",
            )
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"recursion_limit": 6},
            )
            structured = result.get("structured_response") if isinstance(result, dict) else None
            if isinstance(structured, ConstructionFacts):
                return structured.model_copy(
                    update={"source_asset_ids": [item.asset_id for item in assets]}
                )
            if isinstance(structured, dict):
                return ConstructionFacts.model_validate(structured).model_copy(
                    update={"source_asset_ids": [item.asset_id for item in assets]}
                )
            text = str(
                result.get("messages", [])[-1].content
                if isinstance(result, dict) and result.get("messages")
                else result
            )
            payload = json.loads(text[text.find("{") : text.rfind("}") + 1])
            return ConstructionFacts.model_validate(payload).model_copy(
                update={"source_asset_ids": [item.asset_id for item in assets]}
            )
        except Exception as exc:
            raise ProviderError(f"ConstructionFacts 汇总失败: {exc}") from exc

    def _enforce_color_verdict(
        self,
        facts: ConstructionFacts,
        assets: list[VisualAsset],
        observations: list[ExtractionResult],
    ) -> ConstructionFacts:
        """Make the deterministic color check authoritative after LLM synthesis."""

        assets_by_id = {asset.asset_id: asset for asset in assets}
        checks: list[tuple[bool | None, float]] = []
        for observation in observations:
            asset = assets_by_id.get(observation.asset_id)
            if asset is None:
                continue
            candidates = [item for item in observation.elements if item.type == "seal_candidate"]
            if not candidates:
                continue
            check = self.color_verifier.verify(
                asset.image_bytes,
                candidates[0].bbox,
            )
            checks.append((check.is_red, check.red_ratio))
        if not checks:
            seal = facts.seal.model_copy(
                update={
                    "is_red": None,
                    "color_ratio": None,
                    "color_method": "hsv_pixel_ratio",
                }
            )
            return facts.model_copy(update={"seal": seal})
        verdicts = [value for value, _ in checks]
        if any(value is True for value in verdicts):
            is_red: bool | None = True
        elif all(value is False for value in verdicts):
            is_red = False
        else:
            is_red = None
        best_ratio = max(ratio for _, ratio in checks)
        seal = facts.seal.model_copy(
            update={
                "is_red": is_red,
                "color_ratio": best_ratio,
                "color_method": "hsv_pixel_ratio",
            }
        )
        return facts.model_copy(update={"seal": seal})

    @staticmethod
    def _entity_dict(entity: EhsConstruct | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(entity, Mapping):
            return dict(entity)
        return cast(dict[str, Any], entity.model_dump(by_alias=True))

    @staticmethod
    def _status(findings: list[RuleFinding]) -> ReviewStatus:
        if any(item.status == FindingStatus.UNAVAILABLE for item in findings):
            return ReviewStatus.UNAVAILABLE
        if any(
            item.severity == FindingSeverity.REJECT and item.status == FindingStatus.FAIL
            for item in findings
        ):
            return ReviewStatus.REJECT
        if any(item.status == FindingStatus.REVIEW_REQUIRED for item in findings):
            return ReviewStatus.REVIEW_REQUIRED
        if any(item.status == FindingStatus.FAIL for item in findings):
            return ReviewStatus.PASS_WITH_SUGGESTIONS
        return ReviewStatus.PASS

    @staticmethod
    def _mark_unavailable_findings(
        findings: list[RuleFinding], failed_assets: int
    ) -> list[RuleFinding]:
        if not failed_assets:
            return findings
        return [
            item.model_copy(
                update={
                    "status": FindingStatus.REVIEW_REQUIRED
                    if item.status == FindingStatus.PASS
                    else item.status,
                    "message": item.message or f"{failed_assets} 个视觉资产识别失败，结果需复核",
                }
            )
            for item in findings
        ]

    @staticmethod
    def _unavailable(
        run_id: str,
        message: str,
        *,
        file_count: int,
        asset_count: int = 0,
        failed_asset_count: int = 0,
        retry_count: int = 0,
    ) -> ReviewReport:
        return ReviewReport(
            run_id=run_id,
            status=ReviewStatus.UNAVAILABLE,
            summary={
                "reject_count": 0,
                "suggestion_count": 0,
                "reminder_count": 0,
                "review_required_count": 0,
            },
            findings=[
                RuleFinding(
                    rule_id="SYSTEM",
                    severity=FindingSeverity.REJECT,
                    status=FindingStatus.UNAVAILABLE,
                    message=message,
                )
            ],
            processing=ProcessingStats(
                file_count=file_count,
                asset_count=asset_count,
                failed_asset_count=failed_asset_count,
                retry_count=retry_count,
            ),
            created_at=datetime.now(UTC),
        )


__all__ = ["ArtifactStore", "ConstructionPlanReviewOrchestrator"]
