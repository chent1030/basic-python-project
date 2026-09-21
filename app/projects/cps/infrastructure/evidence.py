from __future__ import annotations

import base64
import binascii
import io
import math
import os
import warnings

import httpx
from PIL import Image, UnidentifiedImageError

from .config import EmbeddingsConfig


def validate_image(encoded: str, max_bytes: int) -> tuple[bytes, str, int, int]:
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Evidence must be strict base64 image content") from exc
    if not content or len(content) > max_bytes:
        raise ValueError("Image size exceeds the configured upload limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP"):
                    raise ValueError("Only PNG, JPEG and WEBP are accepted")
                width, height = image.size
                if width * height > 25_000_000:
                    raise ValueError("Image exceeds the 25 megapixel limit")
                mime = Image.MIME[image.format]
                image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid or unsafe image") from exc
    return content, mime, width, height


class HTTPImageEncoder:
    def __init__(self, config: EmbeddingsConfig):
        self.config = config

    async def encode(self, image: str) -> list[float]:
        config = self.config
        if not config.url:
            raise ValueError("Image embedding service is not configured")
        headers = {}
        if config.api_key_env:
            key = os.environ.get(config.api_key_env)
            if not key:
                raise ValueError("Image embedding credential is missing")
            headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=config.timeout, follow_redirects=False) as client:
            response = await client.post(
                config.url, headers=headers, json={"model": config.model, "image": image}
            )
            response.raise_for_status()
        embedding = response.json()["embedding"]
        if not isinstance(embedding, list) or not 1 <= len(embedding) <= config.max_dimensions:
            raise ValueError("Invalid image embedding dimensions")
        vector = [float(value) for value in embedding]
        if not all(math.isfinite(value) for value in vector) or not any(vector):
            raise ValueError("Image embedding must be finite and nonzero")
        return vector
