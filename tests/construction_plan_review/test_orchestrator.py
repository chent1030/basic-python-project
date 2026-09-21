from __future__ import annotations

import io

import pytest
from PIL import Image

from app.projects.construction_plan_review.assets import PreviewFile, _image_asset
from app.projects.construction_plan_review.models import (
    ConstructionFacts,
    ExtractionResult,
    ReviewConfig,
    SealFact,
    VisualElement,
)
from app.projects.construction_plan_review.orchestrator import ConstructionPlanReviewOrchestrator
from app.projects.construction_plan_review.providers import ProviderError, ProviderRegistry


def _image_bytes() -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (120, 80), "white")
    for x in range(30, 90):
        for y in range(20, 60):
            image.putpixel((x, y), (255, 0, 0))
    image.save(output, format="PNG")
    return output.getvalue()


class FlakyProvider:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, asset, *, model: str, attempt: int = 1) -> ExtractionResult:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("temporary")
        return ExtractionResult(
            asset_id=asset.asset_id,
            provider=self.name,
            model=model,
            raw_text="ok",
            elements=[VisualElement(type="text", text="ok", confidence=0.99)],
        )


@pytest.mark.asyncio
async def test_extraction_retries_one_asset() -> None:
    provider = FlakyProvider()
    registry = ProviderRegistry({"flaky": provider})
    orchestrator = ConstructionPlanReviewOrchestrator(providers=registry)
    file = PreviewFile(file_id="a", file_name="a.png", preview_url="https://s3/a")
    visual_assets = _image_asset(_image_bytes(), file=file, index=0)

    results, retries = await orchestrator._extract_assets(
        [visual_assets], ReviewConfig(provider="flaky", max_retries=1)
    )
    assert results[0].error is None
    assert provider.calls == 2
    assert retries == 1


@pytest.mark.asyncio
async def test_run_without_preview_is_unavailable() -> None:
    report = await ConstructionPlanReviewOrchestrator().run(
        {"constructionProgrammeFileInfoList": []}
    )
    assert report.status == "UNAVAILABLE"
    assert report.findings[0].rule_id == "SYSTEM"


def test_color_analysis_overrides_llm_color_label() -> None:
    orchestrator = ConstructionPlanReviewOrchestrator()
    file = PreviewFile(file_id="a", file_name="a.png", preview_url="https://s3/a")
    asset = _image_asset(_image_bytes(), file=file, index=0)
    observation = ExtractionResult(
        asset_id=asset.asset_id,
        provider="qwen",
        model="qwen3.8",
        elements=[
            VisualElement(
                type="seal_candidate",
                bbox={"x1": 20, "y1": 10, "x2": 100, "y2": 70},
            )
        ],
    )
    facts = ConstructionFacts(seal=SealFact(present=True, is_red=False))
    result = orchestrator._enforce_color_verdict(facts, [asset], [observation])
    assert result.seal.is_red is True
    assert result.seal.color_method == "hsv_pixel_ratio"
