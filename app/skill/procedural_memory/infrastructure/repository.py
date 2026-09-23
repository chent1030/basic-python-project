"""Procedural 仓储层（FR-11）。

复用 ``memory_entry`` 表（同一 schema，按 source_table='PROCEDURAL' + tags 区分）；

- ``tags`` 必含 ``'procedural'`` + ``f'procedural:{source_id}'``（用于按 pattern 唯一寻址）；
- 同一 ``(category_l1_id, area, decision)`` 三元组 → upsert 复用行；
  ``source_id`` 用稳定 hash（uuid4 → int）作为持久 key；
- ``sample_count`` 累加在 ``payload['sample_count']`` 字段；
- ``last_at`` 写 payload 顶层；``sample_reasons`` 写 payload 内 list，最多 5 条；
- 查询走 ``source_table='PROCEDURAL'`` + 可选 (category_l1_id, area, decision, factory)；
- 时间窗口 ``last_at >= since`` 用于 consolidate。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    String,
    TypeDecorator,
)
from sqlalchemy import and_ as sa_and_
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.skill.procedural_memory.domain.models import (
    SAMPLE_REASONS_MAX,
    DispatchPattern,
    to_jsonb_safe,
)

# 主键列类型：PG 走 BigInteger（对应 BIGSERIAL），SQLite 走 Integer。
_DISPATCH_PK: Any = BigInteger().with_variant(Integer(), "sqlite")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- ORM（独立表，沿用同一 schema）--
#
# 与 memory_entry 字段一致；本模块独立声明 ORM 是为了不强制把 ``PROCEDURAL`` 加到
# ``SourceTable`` 枚举（避免跨仓联动）；runtime 上 storage 写在专属表
# ``dispatch_pattern``，与现有 ``memory_entry`` 同表结构可被 grep / migrate 比对。
# 本波落地用本仓专属表「dispatch_pattern」+ 列对齐 ``memory_entry``，schema
# 一致。 升级路径：``source_table='PROCEDURAL'`` 在 ``memory_entry`` 索引视图中可
# 双写，本表独立维护以保持本模块自治。

PROCEDURAL_SOURCE_TABLE: str = "PROCEDURAL"
TAG_PROCEDURAL: str = "procedural"
TAG_KIND_PROCEDURAL: str = "procedural:dispatch_pattern"


class _TagsArrayType(TypeDecorator):
    """与 memory 一致的 tags 列类型。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_ARRAY(String(64)))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return [str(v) for v in value]

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except (ValueError, TypeError):
                return None
            if isinstance(loaded, list):
                return [str(v) for v in loaded]
        return None


class _PayloadJSONType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


TAGS_TYPE: TypeDecorator = _TagsArrayType()
PAYLOAD_TYPE: TypeDecorator = _PayloadJSONType()


class DispatchPatternORM(Base):
    """Procedural 调度记忆 ORM（独立表，与 memory_entry 列对齐）。"""

    __tablename__ = "dispatch_pattern"

    id: Mapped[int] = mapped_column(_DISPATCH_PK, primary_key=True, autoincrement=True)
    source_table: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issue_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_l1_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_l2_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    factory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(PAYLOAD_TYPE, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(TAGS_TYPE, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.utcnow(),
    )

    __table_args_ = ()


# ---------------------------------------------------------------- helpers --


def _source_id_for(pattern: DispatchPattern) -> int:
    """``(category_l1_id, area, decision)`` → 稳定 int source_id。

    合并键：三者完全一致视为同一 pattern，共享 row + 累加 sample_count。
    """
    basis = f"{pattern.category_l1_id}|{pattern.area}|{pattern.decision}"
    digest = uuid.uuid5(uuid.NAMESPACE_OID, basis).int
    # 64-bit 正整数
    return digest & 0x7FFFFFFFFFFFFFFF


def _row_to_pattern(row: DispatchPatternORM) -> DispatchPattern:
    payload = dict(row.payload or {})
    return DispatchPattern(
        pattern_id=str(payload.get("pattern_id") or row.source_id),
        category_l1_id=int(payload.get("category_l1_id") or row.category_l1_id or 0),
        category_l2_id=payload.get("category_l2_id", row.category_l2_id),
        factory=payload.get("factory", row.factory),
        area=str(payload.get("area") or row.area or ""),
        decision=str(payload.get("decision") or ""),
        ai_relation=str(payload.get("ai_relation") or ""),
        sample_count=int(payload.get("sample_count") or 1),
        last_at=str(payload.get("last_at") or ""),
        sample_reasons=list(payload.get("sample_reasons") or []),
    )


def _build_tags(source_id: int, factory: str | None) -> list[str]:
    """包含 ``procedural`` 类别 + ``procedural:<source_id>`` 唯一 tag。"""
    extras: list[str] = [TAG_PROCEDURAL, f"procedural:{source_id}"]
    if factory:
        extras.append(f"factory:{factory}")
    return [TAG_KIND_PROCEDURAL, *extras]


# ---------------------------------------------------------------- Repository --


class ProceduralRepository:
    """Procedural 仓储——upsert_pattern / list_patterns / fetch_by_source_id。

    与 ``MemoryRepository`` 同模式：async session_factory 注入，无内部状态，
    单实例可复用。PG / SQLite 走两套等价路径。
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- write ----

    async def upsert_pattern(self, pattern: DispatchPattern) -> DispatchPattern:
        """按 ``(category_l1_id, area, decision)`` upsert——同三元组共享行。

        返回合并后的 pattern（sample_count 累加、sample_reasons 截断到 5 条）。
        """
        source_id = _source_id_for(pattern)
        existing_reasons = list(pattern.sample_reasons)
        async with self._session_factory() as session:
            async with session.begin():
                bind = session.get_bind()
                dialect = getattr(bind, "dialect", None)
                is_pg = bool(dialect and dialect.name == "postgresql")
                if is_pg:
                    payload_dict = _build_payload(
                        pattern, sample_count=pattern.sample_count,
                        reasons=existing_reasons,
                    )
                    stmt = pg_insert(DispatchPatternORM).values(
                        source_table=PROCEDURAL_SOURCE_TABLE,
                        source_id=source_id,
                        issue_id=None,
                        version_no=None,
                        category_l1_id=pattern.category_l1_id,
                        category_l2_id=pattern.category_l2_id,
                        factory=pattern.factory,
                        area=pattern.area,
                        severity=None,
                        payload=payload_dict,
                        tags=_build_tags(source_id, pattern.factory),
                        is_active=True,
                    ).on_conflict_do_update(
                        index_elements=["source_table", "source_id"],
                        set_={
                            "category_l1_id": pattern.category_l1_id,
                            "category_l2_id": pattern.category_l2_id,
                            "factory": pattern.factory,
                            "area": pattern.area,
                            "payload": _build_payload(
                                pattern, sample_count=pattern.sample_count,
                                reasons=existing_reasons,
                            ),
                            "tags": _build_tags(source_id, pattern.factory),
                        },
                    )
                    await session.execute(stmt)
                else:
                    existing = await self._get_row(session, source_id)
                    if existing is None:
                        new_payload = _build_payload(
                            pattern, sample_count=pattern.sample_count,
                            reasons=existing_reasons,
                        )
                        existing = DispatchPatternORM(
                            source_table=PROCEDURAL_SOURCE_TABLE,
                            source_id=source_id,
                            category_l1_id=pattern.category_l1_id,
                            category_l2_id=pattern.category_l2_id,
                            factory=pattern.factory,
                            area=pattern.area,
                            payload=new_payload,
                            tags=_build_tags(source_id, pattern.factory),
                            is_active=True,
                        )
                        session.add(existing)
                    else:
                        old_payload = dict(existing.payload or {})
                        old_count = int(old_payload.get("sample_count") or 0)
                        old_reasons = list(old_payload.get("sample_reasons") or [])
                        merged_reasons = _merge_reasons(old_reasons, existing_reasons)
                        merged_payload = _build_payload(
                            pattern,
                            sample_count=old_count + pattern.sample_count,
                            reasons=merged_reasons,
                        )
                        existing.category_l1_id = pattern.category_l1_id
                        existing.category_l2_id = pattern.category_l2_id
                        existing.factory = pattern.factory
                        existing.area = pattern.area
                        existing.payload = merged_payload
                        existing.tags = _build_tags(source_id, pattern.factory)
            await session.flush()
            row = await self._get_row(session, source_id)
            if row is None:
                raise RuntimeError("upsert 后未找到 dispatch_pattern 行")
            return _row_to_pattern(row)

    async def _get_row(
        self, session: AsyncSession, source_id: int,
    ) -> DispatchPatternORM | None:
        stmt = sa_select(DispatchPatternORM).where(
            sa_and_(
                DispatchPatternORM.source_table == PROCEDURAL_SOURCE_TABLE,
                DispatchPatternORM.source_id == source_id,
            ),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # -------------------------------------------------------------- read ----

    async def fetch_by_source_id(self, source_id: int) -> DispatchPattern | None:
        async with self._session_factory() as session:
            row = await self._get_row(session, source_id)
            if row is None:
                return None
            return _row_to_pattern(row)

    async def list_patterns(
        self,
        *,
        category_l1_id: int | None = None,
        area: str | None = None,
        decision: str | None = None,
        factory: str | None = None,
        limit: int = 200,
    ) -> list[DispatchPattern]:
        """按 (category_l1_id, area, decision, factory) 过滤列出 pattern。"""
        async with self._session_factory() as session:
            stmt = sa_select(DispatchPatternORM).where(
                sa_and_(
                    DispatchPatternORM.source_table == PROCEDURAL_SOURCE_TABLE,
                    DispatchPatternORM.is_active.is_(True),
                ),
            )
            if category_l1_id is not None:
                stmt = stmt.where(
                    DispatchPatternORM.category_l1_id == category_l1_id,
                )
            if area is not None:
                stmt = stmt.where(DispatchPatternORM.area == area)
            if factory is not None:
                stmt = stmt.where(DispatchPatternORM.factory == factory)
            if decision is not None:
                # decision 写进 payload——SQLite 不支持 JSON 路径过滤，应用层补筛
                rows = list((await session.execute(stmt.limit(limit))).scalars().all())
                return [
                    _row_to_pattern(r) for r in rows
                    if str((r.payload or {}).get("decision") or "") == decision
                ]
            stmt = stmt.order_by(DispatchPatternORM.id.desc()).limit(limit)
            result = await session.execute(stmt)
            return [_row_to_pattern(r) for r in result.scalars().all()]

    async def list_since(self, since: datetime, *, limit: int = 500) -> list[DispatchPattern]:
        """consolidate 用——拉最近 ``since`` 之后的 pattern。"""
        async with self._session_factory() as session:
            stmt = sa_select(DispatchPatternORM).where(
                sa_and_(
                    DispatchPatternORM.source_table == PROCEDURAL_SOURCE_TABLE,
                    DispatchPatternORM.is_active.is_(True),
                    DispatchPatternORM.created_at >= since,
                ),
            ).order_by(DispatchPatternORM.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [_row_to_pattern(r) for r in result.scalars().all()]

    async def reset_pattern(self, pattern: DispatchPattern) -> DispatchPattern:
        """consolidate 后写入——把合并后 sample_count 写回（覆盖而非累加）。"""
        source_id = _source_id_for(pattern)
        async with self._session_factory() as session:
            async with session.begin():
                bind = session.get_bind()
                dialect = getattr(bind, "dialect", None)
                is_pg = bool(dialect and dialect.name == "postgresql")
                if is_pg:
                    payload_dict = _build_payload(
                        pattern,
                        sample_count=pattern.sample_count,
                        reasons=list(pattern.sample_reasons),
                    )
                    stmt = pg_insert(DispatchPatternORM).values(
                        source_table=PROCEDURAL_SOURCE_TABLE,
                        source_id=source_id,
                        category_l1_id=pattern.category_l1_id,
                        category_l2_id=pattern.category_l2_id,
                        factory=pattern.factory,
                        area=pattern.area,
                        payload=payload_dict,
                        tags=_build_tags(source_id, pattern.factory),
                        is_active=True,
                    ).on_conflict_do_update(
                        index_elements=["source_table", "source_id"],
                        set_={
                            "category_l1_id": pattern.category_l1_id,
                            "category_l2_id": pattern.category_l2_id,
                            "factory": pattern.factory,
                            "area": pattern.area,
                            "payload": payload_dict,
                            "tags": _build_tags(source_id, pattern.factory),
                        },
                    )
                    await session.execute(stmt)
                else:
                    row = await self._get_row(session, source_id)
                    if row is None:
                        row = DispatchPatternORM(
                            source_table=PROCEDURAL_SOURCE_TABLE,
                            source_id=source_id,
                            category_l1_id=pattern.category_l1_id,
                            category_l2_id=pattern.category_l2_id,
                            factory=pattern.factory,
                            area=pattern.area,
                            payload=_build_payload(
                                pattern,
                                sample_count=pattern.sample_count,
                                reasons=list(pattern.sample_reasons),
                            ),
                            tags=_build_tags(source_id, pattern.factory),
                            is_active=True,
                        )
                        session.add(row)
                    else:
                        row.category_l1_id = pattern.category_l1_id
                        row.category_l2_id = pattern.category_l2_id
                        row.factory = pattern.factory
                        row.area = pattern.area
                        row.payload = _build_payload(
                            pattern,
                            sample_count=pattern.sample_count,
                            reasons=list(pattern.sample_reasons),
                        )
                        row.tags = _build_tags(source_id, pattern.factory)
            await session.flush()
            row = await self._get_row(session, source_id)
            if row is None:
                raise RuntimeError("reset_pattern 后未找到行")
            return _row_to_pattern(row)


def _build_payload(
    pattern: DispatchPattern, *, sample_count: int, reasons: list[str],
) -> dict[str, Any]:
    trimmed = reasons[:SAMPLE_REASONS_MAX]
    payload = {
        "pattern_id": pattern.pattern_id,
        "category_l1_id": pattern.category_l1_id,
        "category_l2_id": pattern.category_l2_id,
        "factory": pattern.factory,
        "area": pattern.area,
        "decision": pattern.decision,
        "ai_relation": pattern.ai_relation,
        "sample_count": sample_count,
        "last_at": pattern.last_at,
        "sample_reasons": trimmed,
        "kind": "procedural_pattern",
    }
    return to_jsonb_safe(payload)


def _merge_reasons(old: list[str], new: list[str]) -> list[str]:
    """合并 sample_reasons：去重 + 优先保留 new，截断到 SAMPLE_REASONS_MAX。"""
    seen: set[str] = set()
    merged: list[str] = []
    for r in list(new) + list(old):
        if not r:
            continue
        key = r.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
        if len(merged) >= SAMPLE_REASONS_MAX:
            break
    return merged


__all__ = [
    "DispatchPatternORM",
    "PROCEDURAL_SOURCE_TABLE",
    "ProceduralRepository",
    "TAG_KIND_PROCEDURAL",
    "TAG_PROCEDURAL",
]