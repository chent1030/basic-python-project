"""Embedding 客户端：抽象 + HashPlaceholder 占位实现。
- 真接入 BGE-large 等真实 Embeddings 时，新增 ``LangChainEmbedding`` 实现 ``EmbeddingClient`` 协议，
  在 ``make_default_embedding_client`` 工厂里按环境变量切换。
- HashPlaceholder 走 SHA256 → 1024 维归一化浮点：仅供契约贯通+测试，生产语义检索
  价值极低（哈希不带语义），TODO：替换真实 Embedding。
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.skill.memory.domain.models import EMBEDDING_DIM

#: Embedding 缺失时返回什么向量——None 还是零向量？None 让上层走「无 hints」路径
#: （prompt v3 中 HISTORICAL_HINT_SECTION 自动跳过），保留可读性与零值迷惑。
EMPTY_EMBEDDING: list[float] | None = None


@runtime_checkable
class EmbeddingClient(Protocol):
    """Embedding 客户端协议。实现：

    - ``embed_documents(texts) -> list[list[float]]`` 批量；
    - ``embed_query(text) -> list[float]`` 单条（接口对称 LangChain Embeddings）。
    """

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...


class HashPlaceholderEmbedding:
    """SHA256 → 1024 维归一化浮点占位实现（仅契约贯通）。

    算法：把 SHA256(seed + i) 字节块当 [0,1] 浮点池，重复直至 1024 维，L2 归一化。
    同样的输入文本永远产出同样的向量——embedding 检索可工作，但**不带语义**。
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        if not isinstance(text, str):  # 容错：dict/list/None 走 repr
            text = repr(text)
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        out: list[float] = []
        i = 0
        # 每次 SHA256(seed + bytes(i)) 取 8 字节做 IEEE 754 double bits → [0,1)
        while len(out) < self._dim:
            block = hashlib.sha256(seed + i.to_bytes(8, "big", signed=False)).digest()
            for j in range(0, len(block), 8):
                if len(out) >= self._dim:
                    break
                chunk = block[j : j + 8]
                if len(chunk) < 8:
                    chunk = chunk + b"\x00" * (8 - len(chunk))
                val_int = int.from_bytes(chunk, "big", signed=False)
                # 转 [0,1)
                out.append(val_int / 2**64)
            i += 1
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


def make_default_embedding_client(env: Any | None = None) -> EmbeddingClient:
    """工厂：环境变量有真实 endpoint 则走 LangChain；否则 HashPlaceholder。

    TODO[embedding-real]：接 BGE-large（1024 维）后，按 ``EMBEDDING_ENDPOINT_URL``
    + ``EMBEDDING_MODEL`` 环境变量实例化 LangChain Embeddings 客户端。
    """
    return HashPlaceholderEmbedding()


__all__ = [
    "EMPTY_EMBEDDING",
    "EmbeddingClient",
    "HashPlaceholderEmbedding",
    "make_default_embedding_client",
]