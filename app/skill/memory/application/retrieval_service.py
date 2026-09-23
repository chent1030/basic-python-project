"""记忆 retrieval 服务：issue 上下文检索 + pattern 提示检索。

- ``retrieve_context_for_issue``：按 issue_dto 构造查询向量 → 字段过滤 + 余弦检索 →
  返回 hint 列表（带 score）→ 上层 prompt 拼接使用；
- ``retrieve_pattern_hint``：语义层 pattern 检索，给 AI 初审提示「历史同类如何处理」；
- ``format_hints_for_prompt``：把 hints 拍成 prompt 可用 JSON 字符串（缺失 hints 时返回 None，
  上层跳过 HISTORICAL_HINT_SECTION）。

失败兜底：任何异常返回空列表/None，不阻断主流程（AI 初审永不依赖 memory）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import MemoryEntry
from app.skill.memory.infrastructure.embedding_client import EmbeddingClient
from app.skill.memory.infrastructure.repository import MemoryRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievalHint:
    """单条检索结果（带 score + 序列化文本）。"""

    entry: MemoryEntry
    score: float

    def to_prompt_dict(self, max_payload_chars: int = 240) -> dict[str, Any]:
        """渲染到 prompt 中的最小化字典。"""
        payload = dict(self.entry.payload or {})
        if max_payload_chars > 0:
            for k, v in list(payload.items()):
                sv = str(v)
                if len(sv) > max_payload_chars:
                    payload[k] = sv[: max_payload_chars - 1] + "…"
        return {
            "source_table": self.entry.source_table,
            "source_id": self.entry.source_id,
            "issue_id": self.entry.issue_id,
            "category_l1_id": self.entry.category_l1_id,
            "category_l2_id": self.entry.category_l2_id,
            "factory": self.entry.factory,
            "area": self.entry.area,
            "severity": self.entry.severity,
            "score": round(self.score, 4),
            "payload": payload,
        }


def _issue_to_text(issue_dto: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "id", "category_l1_id", "category_l2_id",
        "factory", "area", "severity",
    ):
        v = issue_dto.get(key)
        if v is not None:
            parts.append(f"{key}={v}")
    summary = issue_dto.get("summary") or issue_dto.get("description") or issue_dto.get("title")
    if summary:
        parts.append(str(summary))
    if not parts:
        parts.append(repr(issue_dto)[:256])
    return "\n".join(parts)


class MemoryRetrievalService:
    """记忆 retrieval 主入口。"""

    def __init__(
        self,
        repository: MemoryRepository,
        embedding_client: EmbeddingClient,
    ) -> None:
        self._repo = repository
        self._emb = embedding_client

    # -------------------------------------------------------------- issue ----

    async def retrieve_context_for_issue(
        self,
        issue_dto: dict[str, Any],
        *,
        top_k: int = 5,
        kind_filter: list[str] | None = None,
    ) -> list[RetrievalHint]:
        """按 issue 分类/区域/严重度建查询向量 → 字段过滤 → cosine 检索。

        ``kind_filter`` 限定 source_table（默认全部；可传 ``["ADJUDICATION"]`` 只看裁决
        历史，或 ``["EVENT", "ADJUDICATION"]`` 看事件+裁决）。
        """
        try:
            text = _issue_to_text(issue_dto)
            query_vec = await self._emb.embed_query(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 失败（hint 降级为空）: %s: %s", type(exc).__name__, exc)
            return []
        if not query_vec:
            return []

        # kind_filter 决定查哪个 source_table；ALL 则不传 source_table
        out: list[RetrievalHint] = []
        try:
            if kind_filter:
                for st in kind_filter:
                    rows = await self._repo.search_by_embedding(
                        list(query_vec),
                        top_k=top_k,
                        source_table=st,
                        factory=issue_dto.get("factory"),
                        area=issue_dto.get("area"),
                        category_l1_id=issue_dto.get("category_l1_id"),
                        issue_id=issue_dto.get("id"),
                    )
                    out.extend(RetrievalHint(entry=r, score=r.score or 0.0) for r in rows)
                # 多 source_table 合并后按 score 降序截断
                out.sort(key=lambda h: -(h.score))
                return out[:top_k]
            rows = await self._repo.search_by_embedding(
                list(query_vec),
                top_k=top_k,
                factory=issue_dto.get("factory"),
                area=issue_dto.get("area"),
                category_l1_id=issue_dto.get("category_l1_id"),
                issue_id=issue_dto.get("id"),
            )
            return [RetrievalHint(entry=r, score=r.score or 0.0) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("检索失败（hint 降级为空）: %s: %s", type(exc).__name__, exc)
            return []

    # -------------------------------------------------------------- pattern ----

    async def retrieve_pattern_hint(
        self,
        scenario: str,
        *,
        top_k: int = 3,
    ) -> list[RetrievalHint]:
        """语义层 pattern 检索：用于 AI 初审「历史同类如何处理」提示。

        返回的 ``RetrievalHint.entry`` 是 ``MemorySkillPattern``（同样有 score）；
        payload 字段由 ``to_prompt_dict`` 用通用序列化兜底。
        """
        try:
            query_vec = await self._emb.embed_query(scenario)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 失败（pattern 降级为空）: %s: %s", type(exc).__name__, exc)
            return []
        if not query_vec:
            return []
        try:
            patterns = await self._repo.search_patterns_by_embedding(
                list(query_vec), top_k=top_k
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pattern 检索失败（降级为空）: %s: %s", type(exc).__name__, exc)
            return []
        out: list[RetrievalHint] = []
        for p in patterns:
            # 复用 RetrievalHint：把 pattern 适配成「伪 entry」
            pseudo = MemoryEntry(
                source_table="PATTERN",
                source_id=p.id or 0,
                issue_id=None,
                embedding=p.embedding,
                payload={
                    "skill_code": p.skill_code,
                    "pattern_summary": p.pattern_summary,
                    "example_count": p.example_count,
                },
                tags=["pattern"],
            )
            out.append(RetrievalHint(entry=pseudo, score=p.score or 0.0))
        return out

    # -------------------------------------------------------------- prompt ----

    @staticmethod
    def format_hints_for_prompt(hints: list[RetrievalHint] | None) -> str | None:
        """把 hints 拍成 prompt JSON；空 → None。

        上层用法：``hint_str = service.format_hints_for_prompt(hints); prompt =
        template.replace('{hints_json}', hint_str or '')``
        """
        if not hints:
            return None
        try:
            payload = [h.to_prompt_dict() for h in hints]
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hints JSON 序列化失败（降级为空）: %s: %s", type(exc).__name__, exc)
            return None


__all__ = ["MemoryRetrievalService", "RetrievalHint"]


_ = SourceTable  # 保留枚举导出位置供 grep 定位