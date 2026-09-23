"""记忆域 dataclass + pgvector 字符串互转。

设计要点：
- ``MemoryEntry`` 是统一内存表示；DB 入参/出参均经 to_dict/from_dict，dataclass
  内部多一层 ``score``（仅检索时填充，便于 prompt 拼接时排序）；
- embedding 走 ``[v1,v2,v3,...]`` 字符串，跨驱动/跨测试（SQLite 没 vector 类型，
  落 ARRAY[float]）都能互转——pgvector 真接入后切到 ``vector(1024)`` 仅需替换列类型，
  上层无需感知；
- 1024 维 = BGE-large 默认输出维度（任务书已定），改维度会同时破 A/B 测试；
  ``EMBEDDING_DIM`` 单一事实源，校验 embedding_from_str 时硬约束。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: BGE-large 默认输出维度（任务书规定；改维度需同步 pgvector 列宽与所有 embedding 入口）
EMBEDDING_DIM: int = 1024


# ---------------------------------------------------------------- embedding helpers


_VECTOR_RE = re.compile(r"^\[(.*)\]$", re.DOTALL)


def embedding_to_str(vec: list[float] | None) -> str | None:
    """把 list[float] 序列化成 pgvector 字符串 ``[v1,v2,...]``。

    None → None（DB 允许 embedding 为空，配合字段过滤兜底检索）。
    """
    if vec is None:
        return None
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def embedding_from_str(s: str | None) -> list[float] | None:
    """反向。空串 → None；非空必须能解析且维度等于 ``EMBEDDING_DIM``。

    校验失败抛 ``ValueError``——embeddings 是检索索引的根，错位比缺失更危险。
    """
    if s is None or s == "":
        return None
    m = _VECTOR_RE.match(s.strip())
    if not m:
        raise ValueError(f"embedding 字符串格式非法（缺方括号）：{s!r}")
    body = m.group(1).strip()
    if not body:
        return None
    try:
        parts = [float(p.strip()) for p in body.split(",")]
    except ValueError as exc:
        raise ValueError(f"embedding 字符串含非数值 token：{s!r}") from exc
    if len(parts) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding 维度错位：期望 {EMBEDDING_DIM} 实得 {len(parts)}"
        )
    return parts


# ---------------------------------------------------------------- models


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class MemoryEntry:
    """一条记忆。统一内存表示；DB 序列化经 to_dict/from_dict。

    字段口径：
    - ``id`` DB 主键；新建/未持久化前为 None；
    - ``source_table/source_id`` 唯一键（DB 层 UNIQUE），upsert 走这个键；
    - ``embedding`` list[float]（len=EMBEDDING_DIM）或 None；
    - ``tags`` TEXT[]（pgvector 字符串暂未启用，DB 落 ARRAY[Text]）；None→空 list；
    - ``score`` 仅检索路径填充（cosine distance 倒数），不参与持久化；
    - ``superseded_by`` 同源新版本 ID，回填以实现软取代（审计可追）；
    - ``is_active`` 默认 True；supersede 时设为 False。
    """

    source_table: str
    source_id: int
    payload: dict[str, Any]
    id: int | None = None
    issue_id: int | None = None
    version_no: int | None = None
    category_l1_id: int | None = None
    category_l2_id: int | None = None
    factory: str | None = None
    area: str | None = None
    severity: int | None = None
    embedding: list[float] | None = None
    tags: list[str] | None = None
    is_active: bool = True
    superseded_by: int | None = None
    created_at: datetime | None = None
    score: float | None = None  # 仅检索路径填充

    def to_dict(self) -> dict[str, Any]:
        """落 DB / 跨边界传输的统一形态。embedding 序列化为 pgvector 字符串。"""
        return {
            "id": self.id,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "issue_id": self.issue_id,
            "version_no": self.version_no,
            "category_l1_id": self.category_l1_id,
            "category_l2_id": self.category_l2_id,
            "factory": self.factory,
            "area": self.area,
            "severity": self.severity,
            "embedding": embedding_to_str(self.embedding),
            "payload": self.payload,
            "tags": list(self.tags) if self.tags else [],
            "is_active": self.is_active,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at.isoformat() if self.created_at else _utcnow_iso(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MemoryEntry:
        """DB 行 / JSON 字典 → dataclass。embedding 走 embedding_from_str 校验。"""
        emb_raw = raw.get("embedding")
        emb: list[float] | None
        if emb_raw is None:
            emb = None
        elif isinstance(emb_raw, list):
            emb = [float(v) for v in emb_raw]
        elif isinstance(emb_raw, str):
            emb = embedding_from_str(emb_raw)
        else:
            emb = None
        tags_raw = raw.get("tags") or []
        created_at = raw.get("created_at")
        ca: datetime | None
        if isinstance(created_at, datetime):
            ca = created_at
        elif isinstance(created_at, str):
            ca = datetime.fromisoformat(created_at)
        else:
            ca = None
        return cls(
            id=raw.get("id"),
            source_table=raw["source_table"],
            source_id=int(raw["source_id"]),
            issue_id=raw.get("issue_id"),
            version_no=raw.get("version_no"),
            category_l1_id=raw.get("category_l1_id"),
            category_l2_id=raw.get("category_l2_id"),
            factory=raw.get("factory"),
            area=raw.get("area"),
            severity=raw.get("severity"),
            embedding=emb,
            payload=dict(raw["payload"]) if raw.get("payload") else {},
            tags=list(tags_raw),
            is_active=bool(raw.get("is_active", True)),
            superseded_by=raw.get("superseded_by"),
            created_at=ca,
            score=raw.get("score"),
        )


@dataclass(slots=True)
class MemorySkillPattern:
    """语义层聚类产物（single summary + count + 中心向量）。

    ``skill_code`` 唯一键——派生自 (category_l1_id, area, scenario)；
    ``pattern_summary`` 描述「这类问题有什么共性」（由现有证据或上游任务总结）。
    """

    skill_code: str
    pattern_summary: str
    id: int | None = None
    embedding: list[float] | None = None
    example_count: int = 0
    last_seen_at: datetime | None = None
    updated_at: datetime | None = None
    score: float | None = None  # 仅检索路径填充

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill_code": self.skill_code,
            "pattern_summary": self.pattern_summary,
            "embedding": embedding_to_str(self.embedding),
            "example_count": self.example_count,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else _utcnow_iso(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MemorySkillPattern:
        emb_raw = raw.get("embedding")
        emb: list[float] | None
        if emb_raw is None:
            emb = None
        elif isinstance(emb_raw, list):
            emb = [float(v) for v in emb_raw]
        elif isinstance(emb_raw, str):
            emb = embedding_from_str(emb_raw)
        else:
            emb = None

        def _iso(v: Any) -> datetime | None:
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                return datetime.fromisoformat(v)
            return None

        return cls(
            id=raw.get("id"),
            skill_code=raw["skill_code"],
            pattern_summary=raw.get("pattern_summary") or "",
            embedding=emb,
            example_count=int(raw.get("example_count") or 0),
            last_seen_at=_iso(raw.get("last_seen_at")),
            updated_at=_iso(raw.get("updated_at")),
            score=raw.get("score"),
        )


def to_jsonb_safe(obj: Any) -> Any:
    """JSONB 列安全序列化（datetime/dataclass/list → dict/list/str）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, MemoryEntry):
        return obj.to_dict()
    if isinstance(obj, MemorySkillPattern):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: to_jsonb_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonb_safe(v) for v in obj]
    if hasattr("__dict__"):
        return to_jsonb_safe(vars(obj))
    return str(obj)


__all__ = [
    "EMBEDDING_DIM",
    "MemoryEntry",
    "MemorySkillPattern",
    "embedding_from_str",
    "embedding_to_str",
    "to_jsonb_safe",
]


# satisfy ruff unused-import check (json is reserved for future structured payloads)
_ = field, json