"""初审任务应用服务：C-01 提交（幂等）→ 执行（deadline）→ C-02 回调 → C-03 查询。

职责与边界（设计 §3.1/§3.2）：
- 幂等：task_id = ``cps-rectify-{issue_id}-v{version_no}`` 唯一索引兜底；
  同键重放返回同一 task_ref（+replayed 标记）；同键**异参**重放 → ReplayConflict(409)；
- 执行：确定性文本规则先行（毫秒级同步，本波次即全部实现）；
  视觉/语义检查以 NOT_IMPLEMENTED 呈现，不伪造通过；
- deadline：任务级 wall-clock 8 分钟（deadline_at = started_at + 480s），
  asyncio.timeout 到点中断 → FAILED + error_code=TIMEOUT；进程崩溃由 status() 惰性扫描兜底；
- 回调：终态后 fire-and-forget 投递 C-02（可注入 dispatcher 便于测试）；
  重试在 JavaCallbackClient 内完成，与任务计时解耦。

并发口径：本波次执行为请求内联（毫秒级），终态迁移用 ``UPDATE ... WHERE status='RUNNING'``
乐观锁防双执行双回调；同键并发插入由唯一索引兜底（IntegrityError → 重取既有行）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.harness.kernel.domain.models import digest
from app.models.agent_initial_review import AgentInitialReviewExec
from app.projects.initial_review.application.check_pipeline import CheckPipeline
from app.projects.initial_review.domain.models import RectificationReviewRequest
from app.projects.initial_review.domain.text_rules import check_rectification_texts
from app.projects.initial_review.domain.verdicts import (
    aggregate_overall,
    composite_model_version,
)
from app.projects.initial_review.infrastructure.callback import (
    JavaCallbackClient,
    build_callback_payload,
)
from app.projects.initial_review.infrastructure.config import InitialReviewSettings
from app.projects.initial_review.infrastructure.repository import InitialReviewRepository

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class ReplayConflict(Exception):
    """同幂等键但请求参数不一致的重放（→ HTTP 409）。"""

    def __init__(self, task_id: str):
        super().__init__(
            f"幂等键 {task_id} 已存在且请求参数不一致：拒绝覆盖既有初审任务（409）"
        )
        self.task_id = task_id


class CheckRunnerError(Exception):
    """检查执行期异常（→ FAILED + error_code=CHECK_ERROR）。"""


#: 检查执行器：(执行行, 原始请求|None) → (text_checks, model_checks, overall)。
#  request 仅为首次执行可得（base64 附件原文在内存）；进程崩溃/重放路径 request=None，
#  base64 附件届时如实记 SKIPPED/DEGRADED（object_key 模式可从快照重放）。
CheckRunner = Callable[
    [AgentInitialReviewExec, "RectificationReviewRequest | None"],
    Awaitable[tuple[dict, dict, str]],
]
CallbackDispatcher = Callable[[Awaitable[None]], None]


class InitialReviewService:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        callback_client: JavaCallbackClient,
        settings: InitialReviewSettings,
        *,
        clock: Clock | None = None,
        check_runner: CheckRunner | None = None,
        callback_dispatcher: CallbackDispatcher | None = None,
        model_client: Any | None = None,
        rustfs_fetcher: Any | None = None,
        history_loader: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._callback = callback_client
        self._settings = settings
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        self._check_runner: CheckRunner = check_runner or self._default_check_runner
        self._dispatch: CallbackDispatcher = callback_dispatcher or self._default_dispatch
        self._repo = InitialReviewRepository()
        self._pipeline = CheckPipeline(
            settings,
            model_client=model_client,
            rustfs=rustfs_fetcher,
            history_loader=history_loader,
        )

    # ------------------------------------------------------------ C-01 提交 ----

    @staticmethod
    def task_ref(issue_id: str, version_no: int) -> str:
        """幂等键公式（设计 §3.2）：cps-rectify-{issueId}-v{n}。"""
        return f"cps-rectify-{issue_id}-v{version_no}"

    async def submit(self, request: RectificationReviewRequest) -> dict[str, Any]:
        task_id = self.task_ref(request.issue_id, request.version_no)
        fingerprint = self._fingerprint(request)

        created = False
        existing_status: str | None = None
        existing_fingerprint: str | None = None
        async with self._session_factory() as session:
            row: AgentInitialReviewExec | None = None
            try:
                async with session.begin():
                    existing = await self._repo.get_by_task_id(session, task_id)
                    if existing is not None:
                        existing_status = existing.status
                        existing_fingerprint = existing.fingerprint
                    else:
                        now = self._clock()
                        row = AgentInitialReviewExec(
                            task_id=task_id,
                            issue_id=request.issue_id,
                            version_no=request.version_no,
                            submission_id=request.submission_id,
                            status="RUNNING",
                            started_at=now,
                            deadline_at=now + timedelta(seconds=self._settings.deadline_seconds),
                            fingerprint=fingerprint,
                            input_snapshot=self._snapshot(request),
                        )
                        session.add(row)
                        await session.flush()
                        created = True
            except IntegrityError:
                # 并发同键插入：唯一索引兜底 → 回滚后重取既有行（新事务）
                created = False
                existing = await self._repo.get_by_task_id(session, task_id)
                if existing is None:  # pragma: no cover - 竞态窗口极窄
                    raise
                existing_status = existing.status
                existing_fingerprint = existing.fingerprint
                await session.commit()
                row = existing

        if not created and existing_fingerprint != fingerprint:
            raise ReplayConflict(task_id)

        # RUNNING（新建或此前进程崩溃遗留）→ 执行；终态 → 原样返回（幂等重放）
        if existing_status == "RUNNING" or (created and existing_status is None):
            await self._execute(task_id, request=request)

        row = await self._reload(task_id)
        assert row is not None
        return self._submit_response(row, replayed=not created)

    # -------------------------------------------------------------- 执行链 ----

    async def _execute(
        self, task_id: str, *, request: RectificationReviewRequest | None = None
    ) -> AgentInitialReviewExec | None:
        row = await self._reload(task_id)
        if row is None or row.status != "RUNNING":
            return row

        remaining = (row.deadline_at - self._clock()).total_seconds()
        try:
            async with asyncio.timeout(max(remaining, 0.0)):
                # 保证至少一个挂起点：纯同步体也能被 timeout 打断（Python 3.11 语义）
                await asyncio.sleep(0)
                text_checks, model_checks, overall = await self._check_runner(row, request)
        except TimeoutError:
            logger.warning("initial review %s exceeded wall-clock deadline", task_id)
            finished = await self._finish(
                task_id,
                status="FAILED",
                error_code="TIMEOUT",
                error=(
                    f"整任务 wall-clock 超时（{self._settings.deadline_seconds:.0f}s），"
                    "已到点中断（FAILED+timeout 标记，Java 侧可接管）"
                ),
            )
            # 超时也是终态：照常回调，Java 立即可接管（回调 Body 带 error_code=TIMEOUT）
            if finished:
                self._dispatch(self._deliver_callback(task_id))
            return await self._reload(task_id)
        except CheckRunnerError as exc:
            finished = await self._finish(
                task_id, status="FAILED", error_code="CHECK_ERROR", error=str(exc)
            )
            if finished:
                self._dispatch(self._deliver_callback(task_id))
            return await self._reload(task_id)
        except Exception as exc:  # 兜底：任何意外异常不得 500 / 卡 RUNNING 被误标 TIMEOUT
            logger.exception("initial review %s unexpected runner failure", task_id)
            finished = await self._finish(
                task_id,
                status="FAILED",
                error_code="CHECK_ERROR",
                error=f"意外异常：{type(exc).__name__}: {exc}",
            )
            if finished:
                self._dispatch(self._deliver_callback(task_id))
            return await self._reload(task_id)

        finished = await self._finish(
            task_id,
            status="COMPLETED",
            overall=overall,
            model_version=composite_model_version(model_checks),
            text_checks=text_checks,
            model_checks=model_checks,
        )
        if finished:
            # 终态迁移成功方才投递回调（并发执行方败者不重复回调；Java 端另有幂等键兜底）
            self._dispatch(self._deliver_callback(task_id))
        return await self._reload(task_id)

    async def _default_check_runner(
        self, row: AgentInitialReviewExec, request: RectificationReviewRequest | None
    ) -> tuple[dict, dict, str]:
        """波次 2 全量检查：确定性规则（毫秒级）→ A6/A7/A8 管线 → A9 聚合。

        - 模型客户端未装配（单测）或不可用 → 对应检查 SKIPPED+原因，不伪造；
        - overall 聚合见 verdicts.aggregate_overall（任一 FAIL→PROBLEM 等）；
        - 聚合说明（PARTIAL 原因）随 model_checks.aggregate_note 落库并进回调。
        """
        snapshot = row.input_snapshot or {}
        text_checks = check_rectification_texts(
            reason=snapshot.get("reason") or "",
            short_term_measure=snapshot.get("short_term_measure") or "",
            long_term_measure=snapshot.get("long_term_measure") or "",
        )
        serialized = {name: fc.to_dict() for name, fc in text_checks.items()}
        model_checks = await self._pipeline.run(snapshot, request)
        overall, note = aggregate_overall(serialized, model_checks)
        if note:
            model_checks["aggregate_note"] = note
        return serialized, model_checks, overall

    # ------------------------------------------------------------ C-02 回调 ----

    async def _deliver_callback(self, task_id: str) -> None:
        row = await self._reload(task_id)
        if row is None or row.status == "RUNNING":  # pragma: no cover - 防御
            return
        payload = build_callback_payload(row)
        ok, attempts, last_error = await self._callback.send(payload)
        async with self._session_factory() as session, session.begin():
            await self._repo.record_callback(
                session,
                task_id,
                status="SENT" if ok else "FAILED",
                attempts=attempts,
                last_error=last_error,
            )
        if not ok:
            logger.error("C-02 callback for %s failed after retries: %s", task_id, last_error)

    @staticmethod
    def _default_dispatch(coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(_log_task_exception)

    # ------------------------------------------------------------ C-03 查询 ----

    async def status(self, task_id: str) -> dict[str, Any] | None:
        """状态查询；未知名返回 None（router 层转 404）。

        每次查询先做惰性超时扫描（进程崩溃遗留的 RUNNING 行 → FAILED/TIMEOUT），
        兜底「执行进程死掉后任务永远 RUNNING」的故障模式。
        """
        async with self._session_factory() as session, session.begin():
            await self._repo.sweep_expired(session, now=self._clock())
        row = await self._reload(task_id)
        if row is None:
            return None
        return self._status_response(row)

    # ------------------------------------------------------------- 私有工具 ----

    async def _finish(
        self,
        task_id: str,
        *,
        status: str,
        overall: str | None = None,
        model_version: str | None = None,
        text_checks: dict | None = None,
        model_checks: dict | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            return await self._repo.mark_terminal(
                session,
                task_id,
                status=status,
                overall=overall,
                model_version=model_version,
                text_checks=text_checks,
                model_checks=model_checks,
                error=error,
                error_code=error_code,
                finished_at=self._clock(),
            )

    async def _reload(self, task_id: str) -> AgentInitialReviewExec | None:
        async with self._session_factory() as session:
            return await self._repo.get_by_task_id(session, task_id)

    def _fingerprint(self, request: RectificationReviewRequest) -> str:
        """重放参数指纹（对齐 app/projects/cps 的 digest 幂等模式）。"""
        return digest(
            [
                "initial-review-submit",
                request.issue_id,
                request.version_no,
                request.submission_id,
                request.reason,
                request.short_term_measure,
                request.long_term_measure,
                [a.fingerprint_meta() for a in request.before_attachments],
                [a.fingerprint_meta() for a in request.after_attachments],
            ]
        )

    @staticmethod
    def _snapshot(request: RectificationReviewRequest) -> dict[str, Any]:
        """落库请求快照：三文本字段 + 附件元数据（base64 原文剥离，只留 sha256+长度）。"""
        return {
            "issue_id": request.issue_id,
            "submission_id": request.submission_id,
            "version_no": request.version_no,
            "reason": request.reason,
            "short_term_measure": request.short_term_measure,
            "long_term_measure": request.long_term_measure,
            "before_attachments": [a.fingerprint_meta() for a in request.before_attachments],
            "after_attachments": [a.fingerprint_meta() for a in request.after_attachments],
            "issue_snapshot": request.issue_snapshot,
        }

    @staticmethod
    def _submit_response(row: AgentInitialReviewExec, *, replayed: bool) -> dict[str, Any]:
        return {
            "review_task_ref": row.task_id,
            "task_id": row.task_id,
            "status": row.status,
            "overall": row.overall,
            "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
            "replayed": replayed,
        }

    @staticmethod
    def _status_response(row: AgentInitialReviewExec) -> dict[str, Any]:
        return {
            "task_id": row.task_id,
            "review_task_ref": row.task_id,
            "issue_id": row.issue_id,
            "version_no": row.version_no,
            "submission_id": row.submission_id,
            "status": row.status,
            "overall": row.overall,
            "model_version": row.model_version,
            "error": row.error,
            "error_code": row.error_code,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
            "text_checks": row.text_checks,
            "model_checks": row.model_checks,
            "callback_status": row.callback_status,
        }


def _log_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("background task failed: %s", task.exception())
