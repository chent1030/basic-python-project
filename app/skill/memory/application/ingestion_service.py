"""记忆 ingestion 服务：把裁决/事件/问题单写入 memory_entry。

设计要点：
- 全部异步；批量入口（``backfill_since``）串行处理单条（出错不影响整体）；
- 任何单条 ingestion 失败只记日志+errors，不阻断 backfill；上层做 SLO 收敛；
- ``ingest_adjudication`` 是主路径，Java 端 callback 触发频繁，单条走 upsert_judgment；
- EmbeddingClient 协议注入；HashPlaceholder 也工作（语义零价值但检索能跑）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import MemoryEntry
from app.skill.memory.infrastructure.embedding_client import EmbeddingClient
from app.skill.memory.infrastructure.java_client import (
    CpsMemoryClient,
    JavaMemoryFetchError,
)
from app.skill.memory.infrastructure.repository import MemoryRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestionSummary:
    """``backfill_since`` 聚合结果。"""

    adjudications: int = 0
    events: int = 0
    issues: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjudications": self.adjudications,
            "events": self.events,
            "issues": self.issues,
            "errors": list(self.errors),
        }


def _text_for_embedding(d: dict[str, Any]) -> str:
    """把裁决/事件/问题 dict 拍扁成可嵌入的文本。

    顺序：issue_id/分类/区域/严重度 → payload 关键字段 → reason/decision 等。
    """
    parts: list[str] = []
    for key in (
        "issue_id", "category_l1_id", "category_l2_id",
        "factory", "area", "severity", "version_no",
    ):
        v = d.get(key)
        if v is not None:
            parts.append(f"{key}={v}")
    payload = d.get("payload") or {}
    for key in ("decision", "ai_relation", "reason", "reviewer", "event_type", "summary"):
        v = payload.get(key)
        if v is not None:
            parts.append(f"{key}={v}")
    if not parts and isinstance(d.get("payload"), dict):
        parts.extend(f"{k}={v}" for k, v in d["payload"].items())
    if not parts:
        parts.append(repr(d)[:256])
    return "\n".join(parts)


def _entry_from_judgment(d: dict[str, Any], embedding: list[float] | None) -> MemoryEntry:
    payload = {
        "decision": d.get("decision"),
        "ai_relation": d.get("ai_relation"),
        "reason": d.get("reason"),
        "reviewer": d.get("reviewer"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    # 透传 dto 自带的 payload（Java 端可能附带其他裁决上下文），dto 字段优先级更高
    extra = d.get("payload") or {}
    if isinstance(extra, dict):
        payload.update(extra)
    return MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=int(d["id"]),
        issue_id=d.get("issue_id"),
        version_no=d.get("version_no"),
        category_l1_id=d.get("category_l1_id"),
        category_l2_id=d.get("category_l2_id"),
        factory=d.get("factory"),
        area=d.get("area"),
        severity=d.get("severity"),
        embedding=embedding,
        payload=payload,
        tags=d.get("tags") or [],
        is_active=True,
    )


def _entry_from_event(d: dict[str, Any], embedding: list[float] | None) -> MemoryEntry:
    return MemoryEntry(
        source_table=SourceTable.EVENT.value,
        source_id=int(d["id"]),
        issue_id=d.get("issue_id"),
        version_no=d.get("version_no"),
        category_l1_id=d.get("category_l1_id"),
        category_l2_id=d.get("category_l2_id"),
        factory=d.get("factory"),
        area=d.get("area"),
        severity=d.get("severity"),
        embedding=embedding,
        payload=dict(d.get("payload") or {}),
        tags=d.get("tags") or [],
        is_active=True,
    )


def _entry_from_issue(d: dict[str, Any], embedding: list[float] | None) -> MemoryEntry:
    return MemoryEntry(
        source_table=SourceTable.ISSUE.value,
        source_id=int(d["id"]),
        issue_id=int(d["id"]),
        version_no=d.get("version_no"),
        category_l1_id=d.get("category_l1_id"),
        category_l2_id=d.get("category_l2_id"),
        factory=d.get("factory"),
        area=d.get("area"),
        severity=d.get("severity"),
        embedding=embedding,
        payload=dict(d.get("payload") or {}),
        tags=d.get("tags") or [],
        is_active=True,
    )


class MemoryIngestionService:
    """记忆 ingestion 主入口。"""

    def __init__(
        self,
        repository: MemoryRepository,
        embedding_client: EmbeddingClient,
        java_client: CpsMemoryClient | None = None,
    ) -> None:
        self._repo = repository
        self._emb = embedding_client
        self._java = java_client

    # -------------------------------------------------------------- single ----

    async def ingest_adjudication(self, adjudication_dto: dict[str, Any]) -> MemoryEntry:
        text = _text_for_embedding(adjudication_dto)
        embedding = await self._safe_embed(text)
        entry = _entry_from_judgment(adjudication_dto, embedding)
        return await self._repo.upsert_judgment(entry)

    async def ingest_event(self, event_dto: dict[str, Any]) -> MemoryEntry:
        text = _text_for_embedding(event_dto)
        embedding = await self._safe_embed(text)
        entry = _entry_from_event(event_dto, embedding)
        return await self._repo.upsert_episode(entry)

    async def ingest_issue(self, issue_dto: dict[str, Any]) -> MemoryEntry:
        text = _text_for_embedding(issue_dto)
        embedding = await self._safe_embed(text)
        entry = _entry_from_issue(issue_dto, embedding)
        return await self._repo.upsert_issue(entry)

    # -------------------------------------------------------------- batch ----

    async def backfill_since(
        self,
        start_iso: str,
        end_iso: str,
        *,
        kinds: list[str] | None = None,
    ) -> IngestionSummary:
        """从 Java 端批量拉取→逐条 ingest；聚合结果。

        ``kinds`` 默认 ``['adjudications', 'events', 'issues']``；
        任一 kind 缺失对应方法 / 拉取失败 → 记 error 但不阻塞其他 kind。
        """
        kinds = kinds or ["adjudications", "events", "issues"]
        summary = IngestionSummary()

        java_fetch = self._java
        kind_plan = {
            "adjudications": (
                java_fetch.fetch_adjudications if java_fetch else None,
                self.ingest_adjudication,
                "adjudications",
            ),
            "events": (
                java_fetch.fetch_events if java_fetch else None,
                self.ingest_event,
                "events",
            ),
            "issues": (
                java_fetch.fetch_issues if java_fetch else None,
                self.ingest_issue,
                "issues",
            ),
        }

        for kind in kinds:
            entry = kind_plan.get(kind)
            if entry is None:
                summary.errors.append(f"未知 kind: {kind}")
                continue
            fetcher, ingestor, attr = entry
            if fetcher is None:
                summary.errors.append(f"java client 未配置，无法 backfill {kind}")
                continue
            try:
                count = 0
                async for page_resp in self._java.iter_all_pages(fetcher, start_iso, end_iso):
                    items = page_resp.get("items") or page_resp.get("records") or []
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        try:
                            await ingestor(it)
                            count += 1
                        except Exception as exc:  # noqa: BLE001  # 已知单条错误记日志
                            summary.errors.append(
                                f"{kind} ingest 失败 (id={it.get('id')}): "
                                f"{type(exc).__name__}: {exc}",
                            )
                setattr(summary, attr, count)
            except JavaMemoryFetchError as exc:
                summary.errors.append(f"{kind} 拉取失败: {exc}")
                logger.warning("backfill %s 拉取失败: %s", kind, exc)
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"{kind} 异常: {type(exc).__name__}: {exc}")
                logger.exception("backfill %s 异常", kind)
        return summary

    # -------------------------------------------------------------- stale ----

    async def supersede_stale_patterns(self) -> int:
        """扫描 active=true 且 tags 含 ``stale`` 的 entry → 标记 is_active=False。

        返回软删条数。语义：上游（Java 端 / 人工审核 / 业务规则）认为某条记忆已过时，
        在 tags 里追加 ``stale``；本方法负责在 memory 体系内落幕（不影响历史追溯）。
        """
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        from app.skill.memory.infrastructure.repository import (
            MemoryEntryORM,
        )

        async with self._repo._session_factory() as session, session.begin():  # type: ignore[attr-defined]
            stmt = sa_select(MemoryEntryORM).where(MemoryEntryORM.is_active.is_(True))
            result = await session.execute(stmt)
            count = 0
            for row in result.scalars().all():
                tags = list(row.tags or [])
                if "stale" in tags:
                    row.is_active = False
                    count += 1
            await session.flush()
            return count

    # -------------------------------------------------------------- helpers ----

    async def _safe_embed(self, text: str) -> list[float] | None:
        """Embedding 失败不阻断——返回 None 让上层走「无 hints」路径。"""
        try:
            vec = await self._emb.embed_query(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 失败（不影响 ingestion）: %s: %s", type(exc).__name__, exc)
            return None
        if not vec or len(vec) != 1024:
            return None
        return vec


__all__ = ["IngestionSummary", "MemoryIngestionService"]