"""Pluggable visual extraction providers.

The workflow only depends on :class:`ExtractorProvider`; the current default is
Qwen3.8, while another OCR or vision implementation can be registered later.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.messages import HumanMessage

from app.services.llm import llm

from .models import BoundingBox, ExtractionResult, VisualAsset, VisualElement


class ProviderError(RuntimeError):
    """Raised when an extractor cannot produce a valid structured result."""


class ExtractorProvider(Protocol):
    name: str

    async def extract(
        self,
        asset: VisualAsset,
        *,
        model: str,
        attempt: int = 1,
    ) -> ExtractionResult:
        """Extract structured observations from one visual asset."""


def _json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("视觉模型未返回 JSON 对象") from None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("视觉模型返回的 JSON 无法解析") from exc
    if not isinstance(value, dict):
        raise ProviderError("视觉模型返回结果必须是 JSON 对象")
    return value


def _bbox(value: Any) -> BoundingBox | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        values = [value.get(key) for key in ("x1", "y1", "x2", "y2")]
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        values = list(value)
    else:
        return None
    try:
        x1, y1, x2, y2 = (max(0, int(float(item))) for item in values)
    except (TypeError, ValueError):
        return None
    if x2 < x1 or y2 < y1:
        return None
    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)


def _elements(payload: Mapping[str, Any]) -> list[VisualElement]:
    raw_elements = payload.get("elements") or payload.get("observations") or []
    if not isinstance(raw_elements, list):
        raise ProviderError("视觉模型 elements 必须是数组")
    elements: list[VisualElement] = []
    for raw in raw_elements:
        if not isinstance(raw, Mapping):
            continue
        confidence = raw.get("confidence", 0.0)
        try:
            confidence_value = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence_value = 0.0
        known = {"type", "text", "bbox", "confidence", "attributes"}
        attributes = dict(raw.get("attributes") or {})
        attributes.update({str(k): v for k, v in raw.items() if k not in known})
        elements.append(
            VisualElement(
                type=str(raw.get("type") or "text"),
                text=str(raw.get("text") or ""),
                bbox=_bbox(raw.get("bbox")),
                confidence=confidence_value,
                attributes=attributes,
            )
        )
    return elements


class QwenExtractor:
    """Qwen multimodal extractor using the application's OpenAI-compatible LLM service."""

    name = "qwen"

    def __init__(self, provider: str = "qwen") -> None:
        self.provider = provider

    async def extract(
        self,
        asset: VisualAsset,
        *,
        model: str = "qwen3.8",
        attempt: int = 1,
    ) -> ExtractionResult:
        encoded = base64.b64encode(asset.image_bytes).decode("ascii")
        prompt = (
            "请审核这张施工方案图片并只返回 JSON 对象，不要 Markdown。"
            "识别所有可见文字、标题、表格、复选框、手写签字和公章候选区域。"
            "公章候选区域需要输出 type=seal_candidate、bbox 和 seal_text（能读到时）；"
            "不要判断公章是否位于底部。每个元素格式为："
            '{"type":"text|heading|table|checkbox|signature|seal_candidate",'
            '"text":"","bbox":[x1,y1,x2,y2],"confidence":0.0,'
            '"attributes":{}}。根对象格式为 {"elements": [...], "raw_text": "..."}。'
        )
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{asset.mime_type};base64,{encoded}",
                    },
                },
            ]
        )
        try:
            response = await llm.invoke(
                [message],
                provider=self.provider,
                model=model,
                temperature=0.1,
            )
            payload = _json_object(response)
            elements = _elements(payload)
        except ProviderError:
            raise
        except Exception as exc:  # provider/network errors are retried by orchestrator
            raise ProviderError(f"Qwen 视觉识别失败: {exc}") from exc
        return ExtractionResult(
            asset_id=asset.asset_id,
            elements=elements,
            raw_text=str(payload.get("raw_text") or ""),
            provider=self.provider,
            model=model,
            attempt=attempt,
        )


class ProviderRegistry:
    """Small provider registry; no capability matrix or routing rules."""

    def __init__(self, providers: Mapping[str, ExtractorProvider] | None = None) -> None:
        self._providers: dict[str, ExtractorProvider] = {
            "qwen": QwenExtractor(),
            **dict(providers or {}),
        }

    def register(self, name: str, provider: ExtractorProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> ExtractorProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderError(f"未注册识别 Provider: {name}") from exc


__all__ = [
    "ExtractorProvider",
    "ProviderError",
    "ProviderRegistry",
    "QwenExtractor",
]
