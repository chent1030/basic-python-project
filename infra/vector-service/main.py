from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

DB_PATH = os.getenv("VECTOR_DB", "/data/vectors.sqlite")
app = FastAPI(title="CPS Vector Service")


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS vectors (collection TEXT NOT NULL, image_id INTEGER NOT NULL, case_id INTEGER NOT NULL, category_l1_id INTEGER, category_l2_id INTEGER, enabled INTEGER NOT NULL, vector TEXT NOT NULL, PRIMARY KEY (collection, image_id))")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class VectorRequest(BaseModel):
    collection: str = "cps_knowledge_image_vector_siglip2_v1"
    dimension: int | None = None
    metricType: str | None = None
    indexType: str | None = None
    id: int | None = None
    imageId: int | None = None
    caseId: int | None = None
    categoryL1Id: int | None = None
    categoryL2Id: int | None = None
    enabled: bool | None = None
    vector: list[float] = Field(default_factory=list)
    topK: int = 10


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/vector/ensure")
def ensure(request: VectorRequest):
    with db():
        pass
    return {"collection": request.collection, "dimension": request.dimension, "status": "ready"}


@app.post("/v1/vector/load")
def load(request: VectorRequest):
    return {"collection": request.collection, "status": "loaded"}


@app.post("/v1/vector/upsert")
def upsert(request: VectorRequest):
    image_id = request.id if request.id is not None else request.imageId
    with db() as connection:
        connection.execute("INSERT INTO vectors VALUES(?,?,?,?,?,?,?) ON CONFLICT(collection,image_id) DO UPDATE SET case_id=excluded.case_id, category_l1_id=excluded.category_l1_id, category_l2_id=excluded.category_l2_id, enabled=excluded.enabled, vector=excluded.vector", (request.collection, image_id, request.caseId, request.categoryL1Id, request.categoryL2Id, int(request.enabled is not False), json.dumps(request.vector)))
    return {"status": "upserted", "imageId": image_id}


@app.post("/v1/vector/search")
def search(request: VectorRequest):
    query = np.asarray(request.vector, dtype=np.float32)
    results = []
    with db() as connection:
        rows = connection.execute("SELECT image_id,case_id,category_l1_id,category_l2_id,enabled,vector FROM vectors WHERE collection=?", (request.collection,)).fetchall()
    for image_id, case_id, l1, l2, enabled, raw in rows:
        if not enabled:
            continue
        vector = np.asarray(json.loads(raw), dtype=np.float32)
        if vector.shape != query.shape or not np.any(vector):
            continue
        score = float(np.dot(query, vector) / (np.linalg.norm(query) * np.linalg.norm(vector)))
        results.append({"imageId": image_id, "caseId": case_id, "categoryL1Id": l1, "categoryL2Id": l2, "score": score})
    results.sort(key=lambda item: item["score"], reverse=True)
    return {"hits": results[: max(1, min(request.topK, 100))]}
