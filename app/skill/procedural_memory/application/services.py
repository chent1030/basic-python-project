"""Procedural 应用层（FR-11）。

核心方法（API 入口）：

- ``retrieve_dispatch_patterns(query, top_k=5)`` 拉 Java dispatcher → 三段加权
  （category_l1 / area / decision，每段 0.33 权重，缺段按剩余段归一化）→ top_k；
- ``record_pattern(pattern)`` 走 repository upsert（按
  ``(category_l1_id, area, decision)`` 累加 sample_count + sample_reasons）；
- ``consolidate_patterns(since_days=7)`` 拉最近 N 天 → 同
  ``(category_l1_id, area, decision)`` 合并 sample_count + reasons。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.skill.procedural_memory.domain.models import (
    DispatchHint,
    DispatchPattern,
    DispatchQuery,
)
from app.skill.procedural_memory.infrastructure.java_client import ProceduralClient
from app.skill.procedural_memory.infrastructure.repository import ProceduralRepository

logger = logging.getLogger(__name__)


# 三段权重（每段默认 0.33，缺段按剩余归一化）。
_SEGMENT_WEIGHTS: dict[str, float] = {
    "category_l1": 0.34,
    "area": 0.33,
    "decision": 0.33,
}


class ProceduralMemoryService:
    """Procedural 调度记忆应用层——拉 Java + 召回 + upsert + 合并。"""

    def __init__(
        self,
        client: ProceduralClient | None = None,
        repository: ProceduralRepository | None = None,
    ) -> None:
        self._client = client or ProceduralClient()
        self._repository = repository or ProceduralRepository(_DEFAULT_SESSION_FACTORY)

    # -------------------------------------------------------------- retrieve ----

    async def retrieve_dispatch_patterns(
        self,
        scenario: DispatchQuery,
        *,
        top_k: int = 5,
        lookback_days: int = 30,
    ) -> list[DispatchHint]:
        """召回：拉 Java dispatcher 全量分页 → 应用层打分 → top_k。"""
        end_iso = datetime.now(UTC).isoformat()
        start_iso = (
            datetime.now(UTC) - timedelta(days=lookback_days)
        ).isoformat()
        raw_items: list[dict[str, Any]] = []
        async for page in self._client.iter_all_pages(
            start_iso, end_iso, size=100,
            factory=scenario.factory,
            area=scenario.area,
            category_l1_id=scenario.category_l1_id,
            category_l2_id=scenario.category_l2_id,
        ):
            raw_items.extend(page.get("items") or [])

        candidates = [_coerce_pattern(raw) for raw in raw_items]
        scored = [_score(scenario, p) for p in candidates]
        scored.sort(key=lambda h: -h.scenario_similarity_score)
        return scored[: max(1, top_k)]

    # -------------------------------------------------------------- record ----

    async def record_pattern(self, pattern: DispatchPattern) -> DispatchPattern:
        """单条 pattern upsert；repository 内已做 ``(category_l1, area, decision)`` 合并。"""
        return await self._repository.upsert_pattern(pattern)

    # -------------------------------------------------------------- consolidate ----

    async def consolidate_patterns(
        self,
        *,
        since_days: int = 7,
    ) -> dict[str, Any]:
        """扫描最近 ``since_days`` 天 pattern → 合并同三元组 → 写回。

        返回 ``{"merged_count": N, "consolidated_groups": M}``——前者是被合并的
        原始 row 数，后者是合并后的 group 数。
        """
        since = datetime.now(UTC) - timedelta(days=max(1, since_days))
        patterns = await self._repository.list_since(since, limit=2000)

        groups: dict[tuple[int, str, str], list[DispatchPattern]] = {}
        for p in patterns:
            key = (p.category_l1_id, p.area, p.decision)
            groups.setdefault(key, []).append(p)

        merged_count = 0
        consolidated = 0
        for _key, group in groups.items():
            if len(group) <= 1:
                continue
            merged_count += len(group)
            consolidated += 1
            total_count = sum(p.sample_count for p in group)
            seen_reasons: list[str] = []
            seen_set: set[str] = set()
            for p in group:
                for r in p.sample_reasons:
                    key2 = r.strip()
                    if not key2 or key2 in seen_set:
                        continue
                    seen_set.add(key2)
                    seen_reasons.append(key2)
            head = group[0]
            await self._repository.reset_pattern(
                DispatchPattern(
                    pattern_id=head.pattern_id,
                    category_l1_id=head.category_l1_id,
                    category_l2_id=head.category_l2_id,
                    factory=head.factory,
                    area=head.area,
                    decision=head.decision,
                    ai_relation=head.ai_relation,
                    sample_count=total_count,
                    last_at=max(p.last_at for p in group),
                    sample_reasons=seen_reasons,
                ),
            )
        return {
            "merged_count": merged_count,
            "consolidated_groups": consolidated,
            "since_days": since_days,
        }


# ---------------------------------------------------------------- scoring ----


def _score(scenario: DispatchQuery, pattern: DispatchPattern) -> DispatchHint:
    """三段加权（category_l1 / area / decision），缺段按剩余段归一化。

    注：
    - 任意段 query 为 ``None`` 时，不计入分母（视为「不参与评分」）；
    - 只有该段 query 与 pattern 一致时才计入分子；
    - 当 query 完全空（三个段都是 None）时，所有 candidate 都打 0（兜底）。
    """
    parts: list[tuple[str, bool]] = []

    if scenario.category_l1_id is not None:
        parts.append((
            "category_l1",
            scenario.category_l1_id == pattern.category_l1_id,
        ))
    if scenario.area is not None:
        parts.append((
            "area",
            scenario.area == pattern.area,
        ))
    if scenario.decision is not None:
        parts.append((
            "decision",
            scenario.decision == pattern.decision,
        ))

    if not parts:
        score = 0.0
    else:
        denom = sum(_SEGMENT_WEIGHTS[k] for k, _ in parts)
        numer = sum(_SEGMENT_WEIGHTS[k] for k, ok in parts if ok)
        score = numer / denom if denom > 0 else 0.0
    score = max(0.0, min(1.0, score))
    return DispatchHint(
        pattern=pattern,
        scenario_similarity_score=score,
        decision_recommendation=_recommend(pattern),
    )


def _recommend(pattern: DispatchPattern) -> str:
    n = pattern.sample_count
    if n <= 1:
        return f"历史上该 {pattern.area}/{pattern.decision} 仅 1 例，可作参考。"
    if n >= 5:
        return (
            f"{pattern.area}/{pattern.decision} 已有 {n} 例历史裁决，"
            f"建议优先采纳 decision={pattern.decision}（ai_relation={pattern.ai_relation}）。"
        )
    return (
        f"{pattern.area}/{pattern.decision} 累计 {n} 例，建议结合现场情况。"
    )


def _coerce_pattern(raw: dict[str, Any]) -> DispatchPattern:
    """Java dispatcher 单行 → DispatchPattern（payload 容错）。"""
    import uuid as _uuid

    sample_reasons = list(raw.get("sample_reasons") or [])
    return DispatchPattern(
        pattern_id=str(raw.get("pattern_id") or _uuid.uuid4()),
        category_l1_id=int(raw.get("category_l1_id") or 0),
        category_l2_id=raw.get("category_l2_id"),
        factory=raw.get("factory"),
        area=str(raw.get("area") or ""),
        decision=str(raw.get("decision") or ""),
        ai_relation=str(raw.get("ai_relation") or ""),
        sample_count=int(raw.get("sample_count") or 1),
        last_at=str(raw.get("last_at") or datetime.now(UTC).isoformat()),
        sample_reasons=sample_reasons,
    )


# 默认 session_factory——正式环境由调用方注入；测试时 override。
try:
    from app.core.datasource import datasources as _shared  # noqa: WPS433
    _DEFAULT_SESSION_FACTORY = _shared.get_session_factory("postgres_primary")
except Exception:  # pragma: no cover — 启动期降级
    _DEFAULT_SESSION_FACTORY = None  # type: ignore[assignment]


__all__ = ["ProceduralMemoryService"]