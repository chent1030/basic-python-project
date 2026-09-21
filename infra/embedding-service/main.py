from __future__ import annotations

import base64
import io
import os
from functools import lru_cache

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel
from transformers import AutoImageProcessor, AutoModel

MODEL_PATH = os.getenv("MODEL_PATH", "/models/siglip2-so400m-patch14-384")
DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

app = FastAPI(title="CPS SigLIP2 Embedding Service")


class EmbeddingRequest(BaseModel):
    model: str = "siglip2-so400m-patch14-384"
    image: str


@lru_cache(maxsize=1)
def model_bundle():
    processor = AutoImageProcessor.from_pretrained(MODEL_PATH, local_files_only=True)
    model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True)
    model.eval().to(DEVICE)
    return processor, model


def decode_image(value: str) -> Image.Image:
    try:
        encoded = value.split(",", 1)[1] if value.startswith("data:") else value
        return Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))).convert("RGB")
    except Exception as exc:
        raise HTTPException(422, "image must be a valid base64 or data URL image") from exc


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_PATH, "device": DEVICE}


@app.post("/image-embeddings")
def embed(request: EmbeddingRequest):
    processor, model = model_bundle()
    image = decode_image(request.image)
    inputs = processor(images=image, return_tensors="pt")
    inputs = {key: value.to(DEVICE) for key, value in inputs.items()}
    with torch.inference_mode():
        if hasattr(model, "get_image_features"):
            vector = model.get_image_features(**inputs)
        else:
            output = model.vision_model(**inputs).pooler_output
            vector = model.visual_projection(output) if hasattr(model, "visual_projection") else output
        vector = torch.nn.functional.normalize(vector, p=2, dim=-1)[0].float().cpu().tolist()
    return {"model": request.model, "dimension": len(vector), "embedding": vector}
