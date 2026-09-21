"""Deterministic image checks used to verify visual-model conclusions."""

from __future__ import annotations

import colorsys
import io

from pydantic import BaseModel, Field

from .models import BoundingBox


class RedColorResult(BaseModel):
    is_red: bool | None = None
    red_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    sampled_pixels: int = Field(default=0, ge=0)
    method: str = "hsv_pixel_ratio"
    reason: str = ""


class RedSealColorVerifier:
    """Verify red ink with pixels, independent of an LLM's color label.

    The verifier does not infer or validate where a seal should be. If a model
    supplies a candidate box it is used only to reduce unrelated red content.
    """

    def __init__(
        self,
        *,
        red_threshold: float = 0.08,
        non_red_threshold: float = 0.02,
        min_saturation: float = 0.35,
        min_value: float = 0.25,
        max_dimension: int = 700,
    ) -> None:
        if not 0 <= non_red_threshold <= red_threshold <= 1:
            raise ValueError("红色阈值必须满足 0 <= non_red <= red")
        self.red_threshold = red_threshold
        self.non_red_threshold = non_red_threshold
        self.min_saturation = min_saturation
        self.min_value = min_value
        self.max_dimension = max_dimension

    def verify(
        self,
        image_bytes: bytes,
        bbox: BoundingBox | None = None,
    ) -> RedColorResult:
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            if max(image.size) > self.max_dimension:
                scale = self.max_dimension / max(image.size)
                image = image.resize(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                )
                if bbox is not None:
                    bbox = BoundingBox(
                        x1=int(bbox.x1 * scale),
                        y1=int(bbox.y1 * scale),
                        x2=int(bbox.x2 * scale),
                        y2=int(bbox.y2 * scale),
                    )
            if bbox is not None:
                left = max(0, min(image.width, bbox.x1))
                top = max(0, min(image.height, bbox.y1))
                right = max(left, min(image.width, bbox.x2))
                bottom = max(top, min(image.height, bbox.y2))
                image = image.crop((left, top, right, bottom))
            if image.width == 0 or image.height == 0:
                return RedColorResult(reason="公章候选区域为空")
        except Exception as exc:
            return RedColorResult(reason=f"图片颜色分析失败: {exc}")

        red_pixels = 0
        sampled_pixels = 0
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue = pixels[x, y]
                hue, saturation, value = colorsys.rgb_to_hsv(
                    red / 255.0, green / 255.0, blue / 255.0
                )
                if value < self.min_value:
                    continue
                sampled_pixels += 1
                is_red = (hue <= 0.06 or hue >= 0.94) and saturation >= self.min_saturation
                if is_red:
                    red_pixels += 1

        ratio = red_pixels / sampled_pixels if sampled_pixels else 0.0
        if sampled_pixels == 0:
            verdict = None
            reason = "候选区域没有足够的有效像素"
        elif ratio >= self.red_threshold:
            verdict = True
            reason = "红色像素比例达到阈值"
        elif ratio <= self.non_red_threshold:
            verdict = False
            reason = "红色像素比例低于阈值"
        else:
            verdict = None
            reason = "红色像素比例处于不确定区间"
        return RedColorResult(
            is_red=verdict,
            red_ratio=ratio,
            sampled_pixels=sampled_pixels,
            reason=reason,
        )


__all__ = ["RedColorResult", "RedSealColorVerifier"]
