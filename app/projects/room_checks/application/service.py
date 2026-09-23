"""B6 辅房点检判定应用服务（契约 C-04，同步 API）。

判定链（PRD §24.1 照片-only，两阶段）：
1. 取图：object_key → RustFS S3 GET → data URL（复用 initial_review 基建；
   配置缺失 → SKIPPED 不伪造）；
2. 阶段①类型匹配：视觉模型校验照片主体是否为点检项要求的类型；
   不符 → TYPE_MISMATCH（须重拍，不进入合格判断，AC-08）；
3. 阶段②内容判定：按点检内容判断 PASS/FAIL + 理由 + 证据；
   模糊/遮挡 → UNJUDGEABLE（须补拍，AC-09）；
4. 证据留存：stage_trace（各阶段状态/理由/置信度）+ 照片引用 +
   prompt/model 版本锚点，随响应返回由 Java 落执行记录。

超时预算对齐 initial_review 惯例：每阶段单次 ``field_budget_seconds``
（asyncio.timeout，默认 90s，含模型自纠重试 1 次）。
幂等：进程内 LRU+TTL 缓存（与 C-07 同款），键=``room-judge-{submissionId}-
{itemId}-{attempt}``；技术失败不缓存（Java 可同键重试）。
模型配置沿用 ``cps_agent.initial_review.model_check``（qwen vision）与
``.rustfs``（env CPS_STORAGE_* 优先），enabled=false → SKIPPED。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from app.projects.initial_review.infrastructure.config import (
    InitialReviewSettings,
    load_initial_review_settings,
)
from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    ModelUnavailableError,
    RustFSObjectFetcher,
)
from app.projects.room_checks.domain.models import (
    STAGE_CONTENT_JUDGE,
    STAGE_TYPE_MATCH,
    STATUS_JUDGED,
    STATUS_SKIPPED,
    STATUS_TYPE_MISMATCH,
    STATUS_UNJUDGEABLE,
    VERDICT_FAIL,
    VERDICT_PASS,
    ContentJudgeSchema,
    JudgeRequest,
    JudgeResult,
    TypeMatchSchema,
)
from app.projects.room_checks.infrastructure.prompts import (
    ROOM_CONTENT_JUDGE_PROMPT_VERSION,
    ROOM_TYPE_MATCH_PROMPT_VERSION,
    build_content_judge_prompt,
    build_type_match_prompt,
)

logger = logging.getLogger("cps_agent.room_checks")

#: 幂等结果缓存参数（与 C-07 同款：TTL 覆盖点检单提交窗口）
CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 1024

#: 判定链版本锚点（响应 model_version；prompt 版本变更时同步递增）
ROOM_JUDGE_MODEL_VERSION = "room-judge/qwen-vl@1"


class JudgeRequestError(ValueError):
    """入参不合法（照片数超预算等）→ 端点 422。"""


class JudgeTechnicalError(Exception):
    """判定链技术失败（模型调用异常/超时/输出两次无效）→ 端点 502，不缓存。"""


@runtime_checkable
class VisionModelClient(Protocol):
    """B6 所需最小模型接口（DashScopeModelClient/Fake 均满足）。"""

    async def complete_vision_json(
        self, prompt: str, image_data_urls: list[str], schema: type[Any]
    ) -> Any: ...


@runtime_checkable
class PhotoFetcher(Protocol):
    """object_key → data URL（RustFSObjectFetcher 满足）。"""

    async def fetch_data_url(self, object_key: str) -> str: ...


def derive_idempotency_key(submission_id: str, item_id: str, attempt: int) -> str:
    """C-04 契约幂等键：room-judge-{submissionId}-{itemId}-{attempt}。"""
    return f"room-judge-{submission_id}-{item_id}-{attempt}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


class _ResultCache:
    """进程内 LRU+TTL：idempotency_key → JudgeResult（多实例部署改 Redis/DB）。"""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES, ttl: int = CACHE_TTL_SECONDS):
        self._max = max_entries
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[JudgeResult, float]] = OrderedDict()

    def get(self, key: str) -> JudgeResult | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            result, expires_at = entry
            if expires_at <= time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return result

    def put(self, key: str, result: JudgeResult) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (result, time.time() + self._ttl)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


@dataclass(frozen=True)
class _JudgedOutcome:
    """一次成功判定链的内部产物（组装 JudgeResult 用）。"""

    status: str
    verdict: str | None
    reason: str
    evidence: str
    stage_trace: dict[str, Any]


class RoomCheckJudgeService:
    """C-04 服务入口（同步判定，现场等待）。"""

    def __init__(
        self,
        *,
        model_client: VisionModelClient | None = None,
        fetcher: PhotoFetcher | None = None,
        settings: InitialReviewSettings | None = None,
    ) -> None:
        self._settings = settings or load_initial_review_settings()
        self._model_client = model_client
        self._fetcher = fetcher
        self._cache = _ResultCache()

    # -- 惰性装配（测试注入 Fake；生产首次调用时装配真实客户端） ------------
    @property
    def max_photos(self) -> int:
        """照片数量上限（送视觉模型的上下文预算，配置 max_images_per_side）。"""
        return max(1, int(self._settings.model_check.max_images_per_side))

    def _client(self) -> VisionModelClient:
        if self._model_client is None:
            from app.projects.initial_review.infrastructure.model_client import (
                DashScopeModelClient,
            )

            self._model_client = DashScopeModelClient(self._settings.model_check)
        return self._model_client

    def _photo_fetcher(self) -> PhotoFetcher:
        if self._fetcher is None:
            self._fetcher = RustFSObjectFetcher(self._settings.rustfs)
        return self._fetcher

    # -- 主入口 --------------------------------------------------------------
    async def judge(self, req: JudgeRequest) -> JudgeResult:
        cached = self._cache.get(req.idempotency_key)
        if cached is not None:
            return replace(cached, replayed=True)

        if len(req.photo_object_keys) > self.max_photos:
            raise JudgeRequestError(
                f"照片数量 {len(req.photo_object_keys)} 超过模型预算上限 "
                f"{self.max_photos}（max_images_per_side）"
            )

        # 前置条件不具备 → SKIPPED（不伪造判定，A7 原则；结果可缓存重放）
        if not self._settings.model_check.enabled:
            return self._finish_skipped(req, "模型检查已显式禁用（model_check.enabled=false）")
        rustfs_reason = self._settings.rustfs.unavailable_reason
        if rustfs_reason:
            return self._finish_skipped(req, f"照片无法获取：{rustfs_reason}")

        try:
            outcome = await self._run_chain(req)
        except _SkipSignal as exc:
            outcome = self._skipped_outcome(str(exc))
        result = JudgeResult(
            idempotency_key=req.idempotency_key,
            submission_id=req.submission_id,
            item_id=req.item_id,
            attempt=req.attempt,
            status=outcome.status,
            verdict=outcome.verdict,
            reason=outcome.reason,
            evidence=outcome.evidence,
            photo_object_keys=req.photo_object_keys,
            item_snapshot={
                "content": req.item_content,
                "type": req.item_type,
                "deduction": req.deduction,
                "config_version": req.config_version,
            },
            stage_trace=outcome.stage_trace,
            model_version=ROOM_JUDGE_MODEL_VERSION,
            prompt_versions={
                STAGE_TYPE_MATCH: ROOM_TYPE_MATCH_PROMPT_VERSION,
                STAGE_CONTENT_JUDGE: ROOM_CONTENT_JUDGE_PROMPT_VERSION,
            },
            judged_at=_now_iso(),
        )
        self._cache.put(req.idempotency_key, result)
        return result

    # -- 判定链 ----------------------------------------------------------------
    async def _run_chain(self, req: JudgeRequest) -> _JudgedOutcome:
        budget = self._settings.model_check.field_budget_seconds
        image_urls = await self._load_photos(req)
        client = self._client()

        # 阶段①：类型匹配（不符 → 须重拍，不进入合格判断）
        type_prompt = build_type_match_prompt(
            item_type=req.item_type,
            item_content=req.item_content,
            room_name=req.room_name,
            photo_count=len(image_urls),
        )
        try:
            async with asyncio.timeout(budget):
                type_out: TypeMatchSchema = await client.complete_vision_json(
                    type_prompt, image_urls, TypeMatchSchema
                )
        except ModelUnavailableError as exc:
            return self._skipped_outcome(str(exc))
        except (ModelCallError, TimeoutError) as exc:
            raise JudgeTechnicalError(f"类型匹配阶段失败：{exc}") from exc

        type_trace: dict[str, Any] = {
            "status": "PASS" if type_out.type_match else "TYPE_MISMATCH",
            "prompt_version": ROOM_TYPE_MATCH_PROMPT_VERSION,
            "photo_subject": type_out.photo_subject,
            "reason": type_out.reason,
        }
        if not type_out.type_match:
            reason = type_out.reason or f"照片主体不是「{req.item_type}」"
            return _JudgedOutcome(
                status=STATUS_TYPE_MISMATCH,
                verdict=None,
                reason=f"照片与点检项类型不符，须重拍：{reason}",
                evidence=type_out.photo_subject,
                stage_trace={
                    STAGE_TYPE_MATCH: type_trace,
                    STAGE_CONTENT_JUDGE: {
                        "status": "NOT_REACHED",
                        "reason": "类型不匹配，未进入内容判定（AC-08）",
                    },
                },
            )

        # 阶段②：内容判定（PASS/FAIL + 理由/证据；UNJUDGEABLE 须补拍）
        content_prompt = build_content_judge_prompt(
            item_type=req.item_type,
            item_content=req.item_content,
            room_name=req.room_name,
            photo_count=len(image_urls),
        )
        try:
            async with asyncio.timeout(budget):
                content_out: ContentJudgeSchema = await client.complete_vision_json(
                    content_prompt, image_urls, ContentJudgeSchema
                )
        except ModelUnavailableError as exc:
            return self._skipped_outcome(str(exc), type_trace=type_trace)
        except (ModelCallError, TimeoutError) as exc:
            raise JudgeTechnicalError(f"内容判定阶段失败：{exc}") from exc

        content_trace: dict[str, Any] = {
            "status": content_out.verdict,
            "prompt_version": ROOM_CONTENT_JUDGE_PROMPT_VERSION,
            "verdict": content_out.verdict,
            "reason": content_out.reason,
            "evidence": content_out.evidence,
            "confidence": content_out.confidence,
        }
        stage_trace = {STAGE_TYPE_MATCH: type_trace, STAGE_CONTENT_JUDGE: content_trace}
        if content_out.verdict == "UNJUDGEABLE":
            base_reason = content_out.reason or "照片模糊/遮挡，无法判定"
            return _JudgedOutcome(
                status=STATUS_UNJUDGEABLE,
                verdict=None,
                reason=f"{base_reason}（无法判定，须补拍）",
                evidence=content_out.evidence,
                stage_trace=stage_trace,
            )
        verdict = VERDICT_PASS if content_out.verdict == "PASS" else VERDICT_FAIL
        return _JudgedOutcome(
            status=STATUS_JUDGED,
            verdict=verdict,
            reason=content_out.reason,
            evidence=content_out.evidence,
            stage_trace=stage_trace,
        )

    async def _load_photos(self, req: JudgeRequest) -> list[str]:
        """object_key → data URL（配置缺失抛 ModelUnavailableError → SKIPPED）。"""
        fetcher = self._photo_fetcher()
        try:
            return [await fetcher.fetch_data_url(key) for key in req.photo_object_keys]
        except ModelUnavailableError as exc:
            raise _SkipSignal(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - RustFS 网络/签名故障统一技术失败
            raise JudgeTechnicalError(f"照片获取失败：{type(exc).__name__}: {exc}") from exc

    def _skipped_outcome(
        self, reason: str, *, type_trace: dict[str, Any] | None = None
    ) -> _JudgedOutcome:
        trace: dict[str, Any] = {
            STAGE_TYPE_MATCH: type_trace
            or {"status": "SKIPPED", "reason": reason},
            STAGE_CONTENT_JUDGE: {
                "status": "NOT_REACHED",
                "reason": "前置条件缺失，未进入内容判定",
            },
        }
        return _JudgedOutcome(
            status=STATUS_SKIPPED,
            verdict=None,
            reason=f"模型判定不可用（SKIPPED，不伪造）：{reason}",
            evidence="",
            stage_trace=trace,
        )

    def _finish_skipped(self, req: JudgeRequest, reason: str) -> JudgeResult:
        outcome = self._skipped_outcome(reason)
        result = JudgeResult(
            idempotency_key=req.idempotency_key,
            submission_id=req.submission_id,
            item_id=req.item_id,
            attempt=req.attempt,
            status=outcome.status,
            verdict=None,
            reason=outcome.reason,
            evidence="",
            photo_object_keys=req.photo_object_keys,
            item_snapshot={
                "content": req.item_content,
                "type": req.item_type,
                "deduction": req.deduction,
                "config_version": req.config_version,
            },
            stage_trace=outcome.stage_trace,
            model_version=ROOM_JUDGE_MODEL_VERSION,
            prompt_versions={
                STAGE_TYPE_MATCH: ROOM_TYPE_MATCH_PROMPT_VERSION,
                STAGE_CONTENT_JUDGE: ROOM_CONTENT_JUDGE_PROMPT_VERSION,
            },
            judged_at=_now_iso(),
        )
        self._cache.put(req.idempotency_key, result)
        return result


class _SkipSignal(Exception):
    """内部信号：取图阶段前置条件缺失 → 转化为 SKIPPED 结果（不伪造）。"""


__all__ = [
    "CACHE_MAX_ENTRIES",
    "CACHE_TTL_SECONDS",
    "JudgeRequestError",
    "JudgeTechnicalError",
    "ROOM_JUDGE_MODEL_VERSION",
    "RoomCheckJudgeService",
    "derive_idempotency_key",
]
