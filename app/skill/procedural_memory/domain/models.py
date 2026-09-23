"""Procedural 域 dataclass。

设计要点：
- ``DispatchPattern`` 一条「分类 + 区域 + 决策」的程序性记忆（来自裁决/AI 初审的
  累计样例）；
- ``DispatchQuery`` 召回时给定的场景描述（按分类/区域/decision 三段加权匹配）；
- ``DispatchHint`` 召回结果（pattern + scenario_similarity_score +
  decision_recommendation）；
- ``sample_reasons`` 上限 5 条——保留最有代表性的历史理由，避免 payload 膨胀。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- sample_reasons 上限 --


SAMPLE_REASONS_MAX: int = 5


# ---------------------------------------------------------------- models --


@dataclass(slots=True)
class DispatchPattern:
    """Procedural 一条样例（合并后）。

    字段口径：
    - ``pattern_id`` UUID4（Python 侧生成，DB 不强制唯一——合并后沿用首条 id）；
    - ``category_l1_id / category_l2_id`` 必填一级、可选二级；
    - ``factory / area / decision / ai_relation`` 调度四元组；
    - ``sample_count`` 同 ``(category_l1_id, area, decision)`` 累计样例数（consolidate 加）；
    - ``last_at`` 最近一次记录时间 ISO；
    - ``sample_reasons`` 至多 5 条「人类/AI 怎么说」，用于 prompt 复用。
    """

    pattern_id: str
    category_l1_id: int
    category_l2_id: int | None
    factory: str | None
    area: str
    decision: str
    ai_relation: str
    sample_count: int = 1
    last_at: str = field(default_factory=_utcnow_iso)
    sample_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category_l1_id": self.category_l1_id,
            "category_l2_id": self.category_l2_id,
            "factory": self.factory,
            "area": self.area,
            "decision": self.decision,
            "ai_relation": self.ai_relation,
            "sample_count": self.sample_count,
            "last_at": self.last_at,
            "sample_reasons": list(self.sample_reasons),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DispatchPattern:
        reasons = list(raw.get("sample_reasons") or [])
        if len(reasons) > SAMPLE_REASONS_MAX:
            reasons = reasons[:SAMPLE_REASONS_MAX]
        return cls(
            pattern_id=str(raw.get("pattern_id") or ""),
            category_l1_id=int(raw.get("category_l1_id") or 0),
            category_l2_id=raw.get("category_l2_id"),
            factory=raw.get("factory"),
            area=str(raw.get("area") or ""),
            decision=str(raw.get("decision") or ""),
            ai_relation=str(raw.get("ai_relation") or ""),
            sample_count=int(raw.get("sample_count") or 1),
            last_at=str(raw.get("last_at") or _utcnow_iso()),
            sample_reasons=reasons,
        )


@dataclass(slots=True)
class DispatchQuery:
    """Procedural 召回 query——按分类/区域/decision 三段加权匹配。

    ``decision`` 可选：若未给（=None），仅按 category/area 召回，score 中
    decision 段视为 0（不影响其他段）。
    """

    category_l1_id: int | None = None
    category_l2_id: int | None = None
    factory: str | None = None
    area: str | None = None
    decision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DispatchHint:
    """Procedural 召回一条命中。

    - ``pattern`` 命中的程序性记忆；
    - ``scenario_similarity_score`` 0~1，按三段加权（分类 0.34 / 区域 0.33 /
      decision 0.33，缺段省为 0 后按剩余段归一化）；
    - ``decision_recommendation`` 给 AI 初审的简短决策建议——通常是该 pattern 的
      决策文本 + sample_count 提示。
    """

    pattern: DispatchPattern
    scenario_similarity_score: float
    decision_recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.to_dict(),
            "scenario_similarity_score": round(self.scenario_similarity_score, 4),
            "decision_recommendation": self.decision_recommendation,
        }


def to_jsonb_safe(obj: Any) -> Any:
    """JSONB 列安全序列化（datetime/dataclass/list → dict/list/str）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (DispatchPattern, DispatchHint, DispatchQuery)):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: to_jsonb_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonb_safe(v) for v in obj]
    return str(obj)


__all__ = [
    "DispatchHint",
    "DispatchPattern",
    "DispatchQuery",
    "SAMPLE_REASONS_MAX",
    "to_jsonb_safe",
]