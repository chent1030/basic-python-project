"""记忆仓库层：upsert_episode/upsert_judgment/list_by_issue/search_by_embedding/supersede/cluster_pending。

接口契约：
- 全部 async；入参 dataclass 或 dict；返回 dataclass 或 list；
- ``memory_entry`` 一张表统一承载 episodic(EVENT) + semantic(ADJUDICATION) + 实体(ISSUE)；
- pgvector ``embedding`` 列通过 ``EmbeddingArrayType`` 在 PG 走 ARRAY[float]、SQLite 走 JSON；
- 字符串互转由 ``PgVectorAdapter`` 在 search_by_embedding 路径统一处理。
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import (
    and_ as sa_and_,
)
from sqlalchemy import (
    select as sa_select,
)
from sqlalchemy import (
    update as sa_update,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import MemoryEntry, MemorySkillPattern

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 类型装饰器 --


class EmbeddingArrayType(TypeDecorator):
    """跨方言 embedding 列类型。

    - PostgreSQL: ``ARRAY[FLOAT]``（生产可加 pgvector 扩展支持 cosine_distance 运算符）
    - SQLite / 测试: ``JSON``（存 list[float] 序列化）
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_ARRAY(Float()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return json.dumps([float(v) for v in value])

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except (ValueError, TypeError):
                return None
            if isinstance(loaded, list):
                return [float(v) for v in loaded]
        return None


class TagsArrayType(TypeDecorator):
    """跨方言 tags 列类型（list[str]）。"""

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


class PayloadJSONType(TypeDecorator):
    """跨方言 payload 列类型。PG 走 JSONB，其他走通用 JSON。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


#: 实例复用（SQLAlchemy 列定义处使用类名引用即可，无需显式实例）
EMBEDDING_TYPE: TypeDecorator = EmbeddingArrayType()
TAGS_TYPE: TypeDecorator = TagsArrayType()
PAYLOAD_TYPE: TypeDecorator = PayloadJSONType()

#: 主键列类型：PG 走 BigInteger（对应 BIGSERIAL），SQLite 走 Integer（SQLite 仅
#: INTEGER PRIMARY KEY 才会自动 rowid；BigInteger 在 SQLite 不会触发 autoincrement）。
PK_BIGINT: Any = BigInteger().with_variant(Integer(), "sqlite")


# ---------------------------------------------------------------- ORM 表 ----


class MemoryEntryORM(Base):
    """``memory_entry`` ORM（事件层 + 人工裁决层 + 实体层都用）。"""

    __tablename__ = "memory_entry"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    source_table: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issue_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_l1_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_l2_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    factory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EMBEDDING_TYPE, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(PAYLOAD_TYPE, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(TAGS_TYPE, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("source_table", "source_id", name="uq_memory_entry_source"),
    )


class MemorySkillPatternORM(Base):
    """``memory_skill_pattern`` ORM（语义聚类）。"""

    __tablename__ = "memory_skill_pattern"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    skill_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pattern_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(EMBEDDING_TYPE, nullable=True)
    example_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )


class MemoryMetricSnapshotORM(Base):
    """``memory_metric_snapshot`` ORM（FR-12 评估快照占位，本波不消费）。"""

    __tablename__ = "memory_metric_snapshot"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[dict[str, Any]] = mapped_column(PAYLOAD_TYPE, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )


# ---------------------------------------------------------------- ORM ↔ dataclass --


def _row_to_entry(row: MemoryEntryORM) -> MemoryEntry:
    raw_payload = row.payload or {}
    return MemoryEntry.from_dict(
        {
            "id": row.id,
            "source_table": row.source_table,
            "source_id": row.source_id,
            "issue_id": row.issue_id,
            "version_no": row.version_no,
            "category_l1_id": row.category_l1_id,
            "category_l2_id": row.category_l2_id,
            "factory": row.factory,
            "area": row.area,
            "severity": row.severity,
            "embedding": row.embedding,
            "payload": dict(raw_payload),
            "tags": list(row.tags or []),
            "is_active": row.is_active,
            "superseded_by": row.superseded_by,
            "created_at": row.created_at,
        }
    )


def _row_to_pattern(row: MemorySkillPatternORM) -> MemorySkillPattern:
    return MemorySkillPattern.from_dict(
        {
            "id": row.id,
            "skill_code": row.skill_code,
            "pattern_summary": row.pattern_summary or "",
            "embedding": row.embedding,
            "example_count": row.example_count,
            "last_seen_at": row.last_seen_at,
            "updated_at": row.updated_at,
        }
    )


# ---------------------------------------------------------------- Repository --


class MemoryRepository:
    """记忆仓储层。AsyncSession 注入，单实例可复用（无内部状态）。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------- ingestion ----

    async def upsert_episode(self, entry: MemoryEntry) -> MemoryEntry:
        """事件层 upsert（EVENT）。"""
        return await self._upsert(entry)

    async def upsert_judgment(self, entry: MemoryEntry) -> MemoryEntry:
        """语义层 upsert（ADJUDICATION）。"""
        return await self._upsert(entry)

    async def upsert_issue(self, entry: MemoryEntry) -> MemoryEntry:
        """实体层 upsert（ISSUE）。"""
        return await self._upsert(entry)

    async def _upsert(self, entry: MemoryEntry) -> MemoryEntry:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._upsert_inner(session, entry)
            return _row_to_entry(row)

    async def _upsert_inner(
        self, session: AsyncSession, entry: MemoryEntry
    ) -> MemoryEntryORM:
        bind = session.get_bind()
        dialect = getattr(bind, "dialect", None)
        is_pg = bool(dialect and dialect.name == "postgresql")
        if is_pg:
            stmt = pg_insert(MemoryEntryORM).values(
                source_table=entry.source_table,
                source_id=entry.source_id,
                issue_id=entry.issue_id,
                version_no=entry.version_no,
                category_l1_id=entry.category_l1_id,
                category_l2_id=entry.category_l2_id,
                factory=entry.factory,
                area=entry.area,
                severity=entry.severity,
                embedding=entry.embedding,
                payload=dict(entry.payload),
                tags=list(entry.tags) if entry.tags else [],
                is_active=entry.is_active,
                superseded_by=entry.superseded_by,
            ).on_conflict_do_update(
                constraint="uq_memory_entry_source",
                set_={
                    "issue_id": entry.issue_id,
                    "version_no": entry.version_no,
                    "category_l1_id": entry.category_l1_id,
                    "category_l2_id": entry.category_l2_id,
                    "factory": entry.factory,
                    "area": entry.area,
                    "severity": entry.severity,
                    "embedding": entry.embedding,
                    "payload": dict(entry.payload),
                    "tags": list(entry.tags) if entry.tags else [],
                },
            )
            await session.execute(stmt)
        else:
            existing = await self._get_by_source(
                session, entry.source_table, entry.source_id
            )
            if existing is None:
                existing = MemoryEntryORM(
                    source_table=entry.source_table,
                    source_id=entry.source_id,
                    issue_id=entry.issue_id,
                    version_no=entry.version_no,
                    category_l1_id=entry.category_l1_id,
                    category_l2_id=entry.category_l2_id,
                    factory=entry.factory,
                    area=entry.area,
                    severity=entry.severity,
                    embedding=entry.embedding,
                    payload=dict(entry.payload),
                    tags=list(entry.tags) if entry.tags else [],
                    is_active=entry.is_active,
                    superseded_by=entry.superseded_by,
                )
                session.add(existing)
            else:
                existing.issue_id = entry.issue_id
                existing.version_no = entry.version_no
                existing.category_l1_id = entry.category_l1_id
                existing.category_l2_id = entry.category_l2_id
                existing.factory = entry.factory
                existing.area = entry.area
                existing.severity = entry.severity
                existing.embedding = entry.embedding
                existing.payload = dict(entry.payload)
                existing.tags = list(entry.tags) if entry.tags else []
        await session.flush()
        row = await self._get_by_source(
            session, entry.source_table, entry.source_id
        )
        if row is None:
            raise RuntimeError("upsert 后未找到行——DB 状态异常")
        return row

    async def _get_by_source(
        self, session: AsyncSession, source_table: str, source_id: int
    ) -> MemoryEntryORM | None:
        stmt = sa_select(MemoryEntryORM).where(
            sa_and_(
                MemoryEntryORM.source_table == source_table,
                MemoryEntryORM.source_id == source_id,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------- soft supersede ----

    async def supersede(self, old_id: int, new_id: int) -> bool:
        """软取代：old 行 ``is_active=False``、``superseded_by=new_id``。"""
        async with self._session_factory() as session, session.begin():
            stmt = (
                sa_update(MemoryEntryORM)
                .where(
                    sa_and_(
                        MemoryEntryORM.id == old_id,
                        MemoryEntryORM.is_active.is_(True),
                    )
                )
                .values(is_active=False, superseded_by=new_id)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    # ------------------------------------------------------------- list_by_issue ----

    async def list_by_issue(
        self,
        issue_id: int,
        *,
        source_table: str | None = None,
        include_inactive: bool = False,
    ) -> list[MemoryEntry]:
        """按 issue 列出所有记忆（按 source_table 过滤可选）。"""
        async with self._session_factory() as session:
            stmt = sa_select(MemoryEntryORM).where(MemoryEntryORM.issue_id == issue_id)
            if source_table is not None:
                stmt = stmt.where(MemoryEntryORM.source_table == source_table)
            if not include_inactive:
                stmt = stmt.where(MemoryEntryORM.is_active.is_(True))
            stmt = stmt.order_by(MemoryEntryORM.id.desc())
            result = await session.execute(stmt)
            return [_row_to_entry(r) for r in result.scalars().all()]

    # ------------------------------------------------------------- vector search ----

    async def search_by_embedding(
        self,
        query_vec: list[float],
        *,
        top_k: int = 5,
        source_table: str | None = None,
        factory: str | None = None,
        area: str | None = None,
        category_l1_id: int | None = None,
        issue_id: int | None = None,
    ) -> list[MemoryEntry]:
        """按 embedding cosine 距离排序检索；可选字段过滤。

        生产 PG: ``cosine_distance`` 走 pgvector；
        测试 SQLite: SQL 端拿不到 cosine 距离，改拉全量后在 Python 端用
        ``python_cosine_distance`` 重排（接口契约一致，距离→score 转换不变）。
        """
        from app.skill.memory.infrastructure.pgvector_adapter import (
            PgVectorAdapter,
            python_cosine_distance,
        )

        async with self._session_factory() as session:
            bind = session.get_bind()
            dialect = getattr(bind, "dialect", None)
            is_pg = bool(dialect and dialect.name == "postgresql")
            if is_pg:
                stmt = sa_select(
                    MemoryEntryORM,
                    PgVectorAdapter.distance_expr(
                        MemoryEntryORM.embedding, list(query_vec)
                    ).label("distance"),
                ).where(
                    sa_and_(
                        MemoryEntryORM.is_active.is_(True),
                        MemoryEntryORM.embedding.is_not(None),
                    )
                )
            else:
                stmt = sa_select(MemoryEntryORM).where(
                    sa_and_(
                        MemoryEntryORM.is_active.is_(True),
                        MemoryEntryORM.embedding.is_not(None),
                    )
                )
            if source_table is not None:
                stmt = stmt.where(MemoryEntryORM.source_table == source_table)
            if factory is not None:
                stmt = stmt.where(MemoryEntryORM.factory == factory)
            if area is not None:
                stmt = stmt.where(MemoryEntryORM.area == area)
            if category_l1_id is not None:
                stmt = stmt.where(MemoryEntryORM.category_l1_id == category_l1_id)
            if issue_id is not None:
                stmt = stmt.where(MemoryEntryORM.issue_id == issue_id)
            if not is_pg:
                # 多拉一些上限，避免 Python 重排时丢失候选
                stmt = stmt.limit(max(top_k * 4, 50))
            result = await session.execute(stmt)
            if is_pg:
                rows = result.all()
                out: list[MemoryEntry] = []
                for row, distance in rows:
                    entry = _row_to_entry(row)
                    try:
                        d = float(distance) if distance is not None else 1.0
                    except (TypeError, ValueError):
                        d = 1.0
                    entry.score = max(0.0, 1.0 - d)
                    out.append(entry)
                out.sort(key=lambda e: -(e.score or 0.0))
                return out[:top_k]
            # SQLite 路径
            orm_rows = list(result.scalars().all())
            scored: list[tuple[float, MemoryEntryORM]] = []
            for r in orm_rows:
                if not r.embedding:
                    continue
                d = python_cosine_distance([float(v) for v in r.embedding], list(query_vec))
                scored.append((d, r))
            scored.sort(key=lambda t: t[0])
            out: list[MemoryEntry] = []
            for d, r in scored[:top_k]:
                entry = _row_to_entry(r)
                entry.score = max(0.0, 1.0 - float(d))
                out.append(entry)
            return out

    # ------------------------------------------------------------- clustering ----

    async def cluster_pending(self, limit: int = 200) -> list[MemorySkillPattern]:
        """聚合未聚类的 ADJUDICATION → upsert memory_skill_pattern。

        策略（轻量）：按 (category_l1_id, area) 聚合所有 active ADJUDICATION；
        标记 entry.tags 追加 ``clustered``（下一轮跳过）。
        """
        async with self._session_factory() as session, session.begin():
            stmt = sa_select(MemoryEntryORM).where(
                sa_and_(
                    MemoryEntryORM.source_table == SourceTable.ADJUDICATION.value,
                    MemoryEntryORM.is_active.is_(True),
                )
            ).limit(limit)
            result = await session.execute(stmt)
            rows: list[MemoryEntryORM] = list(result.scalars().all())
            pending = [r for r in rows if not (r.tags and "clustered" in r.tags)]

            buckets: dict[tuple[Any, Any], list[MemoryEntryORM]] = {}
            for r in pending:
                key = (r.category_l1_id, r.area)
                buckets.setdefault(key, []).append(r)

            patterns: list[MemorySkillPattern] = []
            for (cat_l1, area), group in buckets.items():
                code = f"pat:{cat_l1 or 0}:{area or 'NA'}"
                existing = (
                    await session.execute(
                        sa_select(MemorySkillPatternORM).where(
                            MemorySkillPatternORM.skill_code == code
                        )
                    )
                ).scalar_one_or_none()
                # 取该组所有 entry embedding 的均值（轻量中心向量），供 semantic search 使用；
                # 无 embedding 的 entry 自动忽略。
                mean_vec: list[float] | None = None
                embs = [list(r.embedding or []) for r in group if r.embedding]
                if embs:
                    dim = len(embs[0])
                    if all(len(v) == dim for v in embs):
                        mean_vec = [sum(v[i] for v in embs) / len(embs) for i in range(dim)]
                now = datetime.now(UTC)
                if existing is None:
                    new = MemorySkillPatternORM(
                        skill_code=code,
                        pattern_summary=f"分类 {cat_l1} / 区域 {area or 'NA'} 的常见处置",
                        embedding=mean_vec,
                        example_count=len(group),
                        last_seen_at=now,
                        updated_at=now,
                    )
                    session.add(new)
                    await session.flush()
                    patterns.append(_row_to_pattern(new))
                else:
                    existing.example_count += len(group)
                    existing.last_seen_at = now
                    existing.updated_at = now
                    if mean_vec is not None:
                        existing.embedding = mean_vec
                    await session.flush()
                    patterns.append(_row_to_pattern(existing))

                for r in group:
                    tags = list(r.tags or [])
                    if "clustered" not in tags:
                        tags.append("clustered")
                    r.tags = tags
            await session.flush()
            return patterns

    # ------------------------------------------------------------- patterns ----

    async def upsert_pattern(self, pattern: MemorySkillPattern) -> MemorySkillPattern:
        """单条 pattern upsert（外部 cluster 任务用）。"""
        async with self._session_factory() as session, session.begin():
            bind = session.get_bind()
            dialect = getattr(bind, "dialect", None)
            is_pg = bool(dialect and dialect.name == "postgresql")
            now = datetime.now(UTC)
            if is_pg:
                stmt = pg_insert(MemorySkillPatternORM).values(
                    skill_code=pattern.skill_code,
                    pattern_summary=pattern.pattern_summary,
                    embedding=pattern.embedding,
                    example_count=pattern.example_count,
                    last_seen_at=pattern.last_seen_at,
                    updated_at=pattern.updated_at or now,
                ).on_conflict_do_update(
                    index_elements=["skill_code"],
                    set_={
                        "pattern_summary": pattern.pattern_summary,
                        "embedding": pattern.embedding,
                        "example_count": pattern.example_count,
                        "last_seen_at": pattern.last_seen_at,
                        "updated_at": now,
                    },
                )
                await session.execute(stmt)
            else:
                existing = (
                    await session.execute(
                        sa_select(MemorySkillPatternORM).where(
                            MemorySkillPatternORM.skill_code == pattern.skill_code
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    existing = MemorySkillPatternORM(
                        skill_code=pattern.skill_code,
                        pattern_summary=pattern.pattern_summary,
                        embedding=pattern.embedding,
                        example_count=pattern.example_count,
                        last_seen_at=pattern.last_seen_at,
                        updated_at=pattern.updated_at or now,
                    )
                    session.add(existing)
                else:
                    existing.pattern_summary = pattern.pattern_summary
                    existing.embedding = pattern.embedding
                    existing.example_count = pattern.example_count
                    existing.last_seen_at = pattern.last_seen_at
                    existing.updated_at = now
            await session.flush()
            row = (
                await session.execute(
                    sa_select(MemorySkillPatternORM).where(
                        MemorySkillPatternORM.skill_code == pattern.skill_code
                    )
                )
            ).scalar_one()
            return _row_to_pattern(row)

    async def search_patterns_by_embedding(
        self, query_vec: list[float], *, top_k: int = 3
    ) -> list[MemorySkillPattern]:
        """语义层 pattern 检索。

        生产 PG: pgvector cosine_distance；
        测试 SQLite: Python 端 cosine 重排（pgvector 不存在）。
        """
        from app.skill.memory.infrastructure.pgvector_adapter import (
            PgVectorAdapter,
            python_cosine_distance,
        )

        async with self._session_factory() as session:
            bind = session.get_bind()
            dialect = getattr(bind, "dialect", None)
            is_pg = bool(dialect and dialect.name == "postgresql")
            if is_pg:
                stmt = sa_select(
                    MemorySkillPatternORM,
                    PgVectorAdapter.distance_expr(
                        MemorySkillPatternORM.embedding, list(query_vec)
                    ).label("distance"),
                ).where(MemorySkillPatternORM.embedding.is_not(None))
            else:
                stmt = sa_select(MemorySkillPatternORM).where(
                    MemorySkillPatternORM.embedding.is_not(None)
                )
            stmt = stmt.limit(max(top_k * 4, 50) if not is_pg else top_k)
            result = await session.execute(stmt)
            if is_pg:
                out: list[MemorySkillPattern] = []
                for row, distance in result.all():
                    p = _row_to_pattern(row)
                    try:
                        d = float(distance) if distance is not None else 1.0
                    except (TypeError, ValueError):
                        d = 1.0
                    p.score = max(0.0, 1.0 - d)
                    out.append(p)
                out.sort(key=lambda p: -(p.score or 0.0))
                return out[:top_k]
            orm_rows = list(result.scalars().all())
            scored: list[tuple[float, MemorySkillPatternORM]] = []
            for r in orm_rows:
                if not r.embedding:
                    continue
                d = python_cosine_distance([float(v) for v in r.embedding], list(query_vec))
                scored.append((d, r))
            scored.sort(key=lambda t: t[0])
            out: list[MemorySkillPattern] = []
            for d, r in scored[:top_k]:
                p = _row_to_pattern(r)
                p.score = max(0.0, 1.0 - float(d))
                out.append(p)
            return out

    async def list_patterns(self, top_k: int = 50) -> list[MemorySkillPattern]:
        """列出最近 top_k 个 pattern（按 updated_at desc）。"""
        async with self._session_factory() as session:
            stmt = (
                sa_select(MemorySkillPatternORM)
                .order_by(MemorySkillPatternORM.updated_at.desc())
                .limit(top_k)
            )
            result = await session.execute(stmt)
            return [_row_to_pattern(r) for r in result.scalars().all()]


__all__ = [
    "MemoryEntryORM",
    "MemoryMetricSnapshotORM",
    "MemoryRepository",
    "MemorySkillPatternORM",
]