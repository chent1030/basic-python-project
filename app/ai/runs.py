"""运行记录存储(RunStore)—— agent_runs 表(树状)。

每次 agent 运行(trigger/chat,及多拓扑下每个成员/子 agent 调用)都写一条。
通过 parent_run_id + depth 还原整棵调用树。

这是「状态监控」的持久层:与日志(实时)互补,agent_runs 可查询、可做监控面板。
gateway.run() 流程:开始 create(running) → 结束 update(succeeded/failed)。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from app.core.config import settings
from app.core.datasource import datasources
from app.core.logging_config import get_logger
from app.models.agent_run import AgentRun

log = get_logger("app.ai.runs")


def _session_factory():
    return datasources.get_session_factory(settings.agents.session_datasource)


def _new_run_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class RunStore:
    """运行记录读写(树状)。create/update 失败只记日志,不影响主流程。"""

    async def create(
        self,
        *,
        agent_name: str,
        trigger_source: str,
        input_text: str,
        session_id: str | None = None,
        parent_run_id: str | None = None,
        depth: int = 0,
    ) -> str:
        """创建一条 running 状态的运行记录,返回它的 run_id。"""
        run_id = _new_run_id()
        try:
            async with _session_factory()() as session:
                session.add(
                    AgentRun(
                        run_id=run_id,
                        parent_run_id=parent_run_id,
                        depth=depth,
                        agent_name=agent_name,
                        trigger_source=trigger_source,
                        session_id=session_id,
                        input=input_text,
                        status="running",
                    )
                )
                await session.commit()
        except Exception:
            log.exception("创建 agent_run 失败 agent=%s", agent_name)
        return run_id

    async def mark_succeeded(
        self,
        run_id: str,
        *,
        output: str,
        tokens: int | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """标记成功并回填输出/耗时。"""
        await self._finish(run_id, status="succeeded", output=output, tokens=tokens, error=None)

    async def mark_failed(self, run_id: str, *, error: str) -> None:
        """标记失败并回填错误。"""
        await self._finish(run_id, status="failed", output=None, tokens=None, error=error)

    async def _finish(
        self,
        run_id: str,
        *,
        status: str,
        output: str | None,
        tokens: int | None,
        error: str | None,
    ) -> None:
        try:
            now = _now()
            # finished_at 用 Python 侧 now;duration_ms 需要起始时间(由调用方/日志补)
            async with _session_factory()() as session:
                stmt = (
                    update(AgentRun)
                    .where(AgentRun.run_id == run_id)
                    .values(
                        status=status,
                        output=output,
                        tokens=tokens,
                        error=error,
                        finished_at=now,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception:
            log.exception("更新 agent_run 失败 run_id=%s status=%s", run_id, status)

    async def set_duration(self, run_id: str, duration_ms: int) -> None:
        """回填耗时(毫秒)。单独一个方法,gateway 在算完耗时后调。"""
        try:
            async with _session_factory()() as session:
                await session.execute(
                    update(AgentRun)
                    .where(AgentRun.run_id == run_id)
                    .values(duration_ms=duration_ms)
                )
                await session.commit()
        except Exception:
            log.exception("回填 duration_ms 失败 run_id=%s", run_id)

    # ---------- 查询(状态监控/看板用)----------------------------------
    async def list_runs(
        self, agent_name: str, *, limit: int = 50, status: str | None = None,
        include_children: bool = False,
    ) -> list[dict[str, Any]]:
        """列某 agent 的运行记录(最新在前)。

        include_children=True 时,额外包含以这些运行为祖先的成员/子 agent 调用
        (用于复合拓扑的监控视图:看到顶层 agent + 它调用的所有成员)。
        """
        async with _session_factory()() as session:
            stmt = select(AgentRun).where(AgentRun.agent_name == agent_name)
            if status:
                stmt = stmt.where(AgentRun.status == status)
            stmt = stmt.order_by(AgentRun.id.desc()).limit(limit)
            result = await session.execute(stmt)
            roots = list(result.scalars().all())
            root_ids = {r.run_id for r in roots}

            if not include_children or not root_ids:
                return [self._to_dict(r) for r in roots]

            # 取所有节点,客户端侧挑出祖先在 root_ids 里的成员
            all_stmt = select(AgentRun).order_by(AgentRun.id)
            all_rows = list((await session.execute(all_stmt)).scalars().all())
            by_run_id = {r.run_id: r for r in all_rows}

            def _root_in(rid: str) -> str | None:
                cur = rid
                seen: set[str] = set()
                while cur and cur not in seen:
                    seen.add(cur)
                    if cur in root_ids:
                        return cur
                    node = by_run_id.get(cur)
                    cur = node.parent_run_id if node else None
                return None

            out = [self._to_dict(r) for r in roots]
            for r in all_rows:
                if r.run_id in root_ids:
                    continue
                root = _root_in(r.run_id)
                if root:
                    d = self._to_dict(r)
                    d["_root_run_id"] = root
                    out.append(d)
            return out

    async def get_tree(self, root_run_id: str) -> dict[str, Any]:
        """取以某 run_id 为根的整棵调用树。

        先取该根节点,再取所有 parent_run_id 链上可达的后代(按 depth/parent 组织)。
        """
        async with _session_factory()() as session:
            # 根节点
            root_stmt = select(AgentRun).where(AgentRun.run_id == root_run_id)
            root = (await session.execute(root_stmt)).scalar_one_or_none()
            if root is None:
                return {"root": None, "tree": []}
            # 所有以该 root 为祖先的节点:简化为取同 agent 域内 depth>root.depth 的,
            # 再用 parent_run_id 链过滤。这里直接取 parent_run_id 等于 root 或其子嗣。
            # 用递归查询太重,改为:取所有 parent_run_id 非空的候选,客户端侧建树。
            cand_stmt = (
                select(AgentRun)
                .where(AgentRun.depth >= root.depth)
                .order_by(AgentRun.depth, AgentRun.id)
            )
            cands = list((await session.execute(cand_stmt)).scalars().all())

        root_dict = self._to_dict(root)
        # 用 parent 链建树:仅保留 parent_run_id 能回溯到 root 的节点
        by_run_id = {c.run_id: c for c in cands}
        kept: dict[str, Any] = {root.run_id: {**root_dict, "children": []}}

        def _is_descendant(rid: str) -> bool:
            node = by_run_id.get(rid)
            if node is None or node.run_id == root.run_id:
                return node is not None and node.run_id == root.run_id
            if node.parent_run_id == root.run_id:
                return True
            return _is_descendant(node.parent_run_id)  # type: ignore[arg-type]

        for c in cands:
            if c.run_id == root.run_id:
                continue
            if _is_descendant(c.run_id):
                kept[c.run_id] = {**self._to_dict(c), "children": []}

        # 挂载 children
        for rid, node in kept.items():
            parent = node.get("parent_run_id")
            if parent and parent in kept and rid != root.run_id:
                kept[parent]["children"].append(node)

        return {"root": root_dict, "tree": kept[root.run_id]["children"]}

    @staticmethod
    def _to_dict(r: AgentRun) -> dict[str, Any]:
        return {
            "run_id": r.run_id,
            "parent_run_id": r.parent_run_id,
            "depth": r.depth,
            "agent_name": r.agent_name,
            "trigger_source": r.trigger_source,
            "session_id": r.session_id,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "tokens": r.tokens,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "input": (r.input[:500] + "...") if r.input and len(r.input) > 500 else r.input,
            "output": (r.output[:500] + "...") if r.output and len(r.output) > 500 else r.output,
            "error": r.error,
        }


# 单例
run_store = RunStore()


__all__ = ["RunStore", "run_store"]
