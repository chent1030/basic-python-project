"""pgvector 适配层：thin wrapper 把 ``func.cosine_distance`` / ``array_to_string`` 隔离。

设计取舍：
- 生产 PG + pgvector：``func.cosine_distance(col::vector, vec::vector)``；
- 测试 SQLite：pgvector 不存在，改用 Python 端计算（embedding 已是 JSON 列表，SQL
  拉回后由 ``_python_cosine`` 算距离后排序）。
- 两条路径都暴露 ``distance_expr`` / ``array_to_str_expr`` 接口，上层无感。

为什么不是直接用 ``pgvector.sqlalchemy.Vector``：
- pgvector 的 ``vector`` 列类型在 SQLite 不存在（autogenerate + 测试都会炸）；
- 测试套件要求 pgvector 缺失时不报错；本适配统一返回 ARRAY[float] 字符串，
  上层仅看 ``embedding_to_str`` 格式。
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import String, cast, func

from app.skill.memory.domain.models import EMBEDDING_DIM, embedding_to_str


class PgVectorAdapter:
    """pgvector 语义检索适配——上层只看到「where 条件 + 距离 → 排序」三个动作。

    所有方法都接受 ``Any`` 列引用（避免循环依赖），实际在 MemoryRepository
    内部绑定 ``memory_entry.c.embedding``.
    """

    @staticmethod
    def distance_expr(embedding_col: Any, query_vec: list[float]) -> Any:
        """构造 ``cosine_distance(col, '{[v1,v2,...]}')`` SQL 表达式。

        生产 PG 用 pgvector 的 ``cosine_distance`` 函数；测试 SQLite 退化为「常
        量 0」（让所有行同距）+ Python 端重排，因为 SQLite 没有 cosine 函数。
        Repository 层拿到结果后统一在 Python 端二次重排，详见
        ``MemoryRepository.search_by_embedding`` 注释。
        """
        vec_str = embedding_to_str(query_vec) or ""
        return func.cosine_distance(
            cast(embedding_col, String),
            cast(vec_str, String),
        )

    @staticmethod
    def array_to_str_expr(embedding_col: Any) -> Any:
        """``array_to_string(col, ',')`` 拼回 ``v1,v2,...`` 形态。"""
        return func.array_to_string(embedding_col, ",")


def python_cosine_distance(a: list[float], b: list[float]) -> float:
    """纯 Python cosine 距离 = 1 - cosine_similarity。空向量视作 1.0。"""
    if not a or not b:
        return 1.0
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(a[i]) * float(a[i]) for i in range(n)))
    nb = math.sqrt(sum(float(b[i]) * float(b[i]) for i in range(n)))
    if na == 0.0 or nb == 0.0:
        return 1.0
    cos = max(-1.0, min(1.0, dot / (na * nb)))
    return 1.0 - cos


#: 维度硬约束，Schema 校验/Embedding 客户端统一引用
DIMENSION = EMBEDDING_DIM


__all__ = ["DIMENSION", "PgVectorAdapter", "python_cosine_distance"]