"""B6 视觉点检三级判定服务（PRD §23 / §30.1 拍图判断）。

服务名: ``RoomCheckB6JudgeService``（与既有 ``RoomCheckJudgeService`` 的
两阶段 C-04 并存，不破坏现有契约）。三级判定算法：

    ┌─────────────────────┐
    │  evidence 入参        │
    └──────────┬──────────┘
               ▼
    ┌─────────────────────┐
    │ 一级 type_match      │  ← PromptBuilder.build_type_match_prompt
    │ (照片主体 vs 期望)     │     + VisionModelClient.complete_vision
    │ 不符 → PROBLEM/0      │
    └──────────┬──────────┘
               ▼
    ┌─────────────────────┐
    │ 二级 content          │  ← PromptBuilder.build_content_prompt
    │ (关键物证 + 覆盖)      │
    └──────────┬──────────┘
               ▼
    ┌─────────────────────┐
    │ 三级 evidence         │  ← PromptBuilder.build_evidence_prompt
    │ (证据强度 STRONG/WEAK)│
    └──────────┬──────────┘
               ▼
    ┌─────────────────────┐
    │ 评分 + 边界 → 结论    │
    │ ≥80 PASS / 50-79     │
    │ PARTIAL / <50 PROBLEM│
    └─────────────────────┘

设计要点（与设计文档对应）：
- **抗干扰**：三级拆分让模型一次只关注一件事，方差更低；
- **审计可回放**：每级 raw_text 拼进 ``RoomCheckVerdict.raw_output``；
- **降级明确**：技术失败 / 解析失败 → 固定 fallback ``PROBLEM/0`` + reason
  「视觉判定超时/失败，建议人工复检」（与 Java 端约定）；
- **幂等**：进程内 LRU+TTL 缓存（与 C-07 inspection_plans 同款；
  TTL=30 分钟/任务书；max=1024）——同一 retry 短窗内复用 verdict；
- **回调投递**：成功判定后异步 dispatch callback（``asyncio.create_task``，
  与 ``InitialReviewService._default_dispatch`` 一致）。

与既有 2-stage 的关系：
- 2-stage ``RoomCheckJudgeService`` 服务于 Java 同步 C-04 接口（status
  维度 4 状态）；B6 是拍图判断新接口（score 维度 3 结论）；
- 两者模型不同（分数 vs 状态）、路由不同（``/b6-judge`` 与 ``/judge``
  分离），共享 ``RoomCheckJudgeService._ResultCache`` 是不安全的（B6 的
  30 分钟 TTL 远超 C-04 的 24 小时需求），故 B6 单独实现。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    ModelUnavailableError,
)
from app.projects.room_checks.domain.evidence_models import (
    JudgeVerdict,
    RoomCheckEvidence,
    RoomCheckVerdict,
    RoomType,
)
from app.projects.room_checks.infrastructure.callback_client import (
    JavaRoomCheckCallbackClient,
    RoomCheckCallbackClient,
)
from app.projects.room_checks.infrastructure.prompt_builder import (
    PROMPT_VERSION_CONTENT,
    PROMPT_VERSION_EVIDENCE,
    PROMPT_VERSION_TYPE_MATCH,
    PromptBuilder,
)
from app.projects.room_checks.infrastructure.vision_client import (
    QwenVLPlusClient,
    VisionCallResult,
    VisionModelClient,
)

logger = logging.getLogger("cps_agent.room_checks.b6")


#: 三级判定版本锚点（响应 model_version 字段）
B6_MODEL_VERSION = "room-check-b6/qwen-vl-plus@1"

#: 评分公式常量（设计文档约定）
SCORE_BASE = 100
SCORE_DEDUCT_CONTENT_MISMATCH = 40  # 二级 content_match=false
SCORE_DEDUCT_KEYWORDS_MISSING = 20  # 二级 missing_keywords 非空
SCORE_DEDUCT_EVIDENCE_VAGUE = 30  # 三级 evidence_strength=WEAK|NONE
SCORE_DEDUCT_OTHER = 10  # 其他（如证据强度 MODERATE）

#: 边界值（score>=80 PASS / 50-79 PARTIAL / <50 PROBLEM）
SCORE_PASS_THRESHOLD = 80
SCORE_PARTIAL_THRESHOLD = 50

#: 幂等结果缓存参数（任务书：30 分钟 TTL）
CACHE_TTL_SECONDS = 30 * 60
CACHE_MAX_ENTRIES = 1024

#: 单阶段超时（与 initial_review 惯例一致）
DEFAULT_STAGE_TIMEOUT_SECONDS = 60.0

#: fallback reason（与 Java 契约一致）
FALLBACK_TIMEOUT_REASON = "视觉判定超时/失败，建议人工复检"


class JudgeRequestError(ValueError):
    """入参不合法（端点 422）。"""


class JudgeTechnicalError(Exception):
    """判定链技术失败（端点 502，不缓存，同 fingerprint 可重试）。"""


@runtime_checkable
class RoomCheckB6CallbackClient(Protocol):
    """服务异步 dispatch 回调所需的最小接口（实现见 callback_client）。"""

    async def dispatch(self, verdict: RoomCheckVerdict) -> Any: ...


def _now() -> datetime:
    """统一时间源：朴素 UTC（与 sqlite 测试时钟一致；生产 PG 感知型）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _derive_overall(score: int) -> JudgeVerdict:
    """评分→结论映射（设计文档约定边界）。"""
    if score >= SCORE_PASS_THRESHOLD:
        return JudgeVerdict.PASS
    if score >= SCORE_PARTIAL_THRESHOLD:
        return JudgeVerdict.PARTIAL
    return JudgeVerdict.PROBLEM


def _coerce_match_type(expected_match_type: str | None, room_type: RoomType) -> str:
    """无 expected_match_type 时按辅房类型推导默认期望主体。

    推导口径（与 Java 端 cps_room_check_item 默认期望对齐）：
    - PRIMARY → "地面"（主控室常规点检「地面无杂物」）
    - STANDARD → "地面"
    - SPECIAL → "电缆层" / "夹层"
    - TOOL → "工器具"（架/柜/摆放）
    - OTHER → "辅房"（无细化期望，模型自定）
    """
    if expected_match_type and expected_match_type.strip():
        return expected_match_type.strip()
    defaults = {
        RoomType.PRIMARY: "地面",
        RoomType.STANDARD: "地面",
        RoomType.SPECIAL: "电缆夹层",
        RoomType.TOOL: "工器具",
        RoomType.OTHER: "辅房",
    }
    return defaults.get(room_type, "辅房")


class _ResultCache:
    """进程内 LRU+TTL：``fingerprint`` → ``RoomCheckVerdict``。"""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES, ttl: int = CACHE_TTL_SECONDS):
        self._max = max_entries
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[RoomCheckVerdict, float]] = OrderedDict()

    def get(self, key: str) -> RoomCheckVerdict | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            verdict, expires_at = entry
            if expires_at <= time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return verdict

    def put(self, key: str, verdict: RoomCheckVerdict) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (verdict, time.time() + self._ttl)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        """强制清除某 fingerprint 的缓存（rejudge 用）。

        Returns:
            True if there was an entry to clear.
        """
        with self._lock:
            return self._data.pop(key, None) is not None


class RoomCheckB6JudgeService:
    """B6 视觉点检三级判定服务入口。

    与既有 2-stage ``RoomCheckJudgeService`` 并存，不影响 C-04 路由。
    注入：``vision_client``（qwen-vl-plus 适配层）、``callback_client``
    （Java 回调）、``prompt_builder``（三级 prompt 模板）；均提供合理默认。
    """

    def __init__(
        self,
        *,
        vision_client: VisionModelClient | None = None,
        callback_client: RoomCheckCallbackClient | None = None,
        prompt_builder: PromptBuilder | None = None,
        stage_timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS,
        photo_data_url: str | None = None,
        observed_meta: dict[str, Any] | None = None,
    ) -> None:
        self._vision_client: VisionModelClient = vision_client or QwenVLPlusClient()
        self._callback_client: RoomCheckCallbackClient = (
            callback_client or JavaRoomCheckCallbackClient()
        )
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._stage_timeout = max(1.0, float(stage_timeout_seconds))
        # 照片 data URL（真实生产从 RustFS 拉取；测试可注入固定 data URL）
        self._photo_data_url = photo_data_url or "data:image/jpeg;base64,/9j/test"
        # 照片元数据（EXIF/filename/upload tag）——type-match 第一级 prompt 注入
        self._observed_meta = dict(observed_meta or {})
        self._cache = _ResultCache()
        # 待异步消费 callback 协程列表（测试可显式 await；生产 fire-and-forget）
        self._pending_callbacks: list[Awaitable[Any]] = []

    @property
    def cache(self) -> _ResultCache:
        """暴露缓存供 rejudge 端点清除（测试 / 运维观察）。"""
        return self._cache

    @property
    def pending_callbacks(self) -> list[Awaitable[Any]]:
        """待异步 callback 协程列表（与 initial_review ``pending`` 同步模型）。"""
        return list(self._pending_callbacks)

    def drain_pending(self) -> list[Awaitable[Any]]:
        """消费并清空待处理 callback（测试同步用）。"""
        items = list(self._pending_callbacks)
        self._pending_callbacks.clear()
        return items

    # -- 入参校验 --------------------------------------------------------------
    def _validate(self, evidence: RoomCheckEvidence) -> None:
        if not evidence.fingerprint or not evidence.fingerprint.strip():
            raise JudgeRequestError("fingerprint 不能为空")
        if len(evidence.fingerprint) > 256:
            raise JudgeRequestError("fingerprint 超过 256 字符")
        if not evidence.check_item_id or not evidence.check_item_id.strip():
            raise JudgeRequestError("check_item_id 不能为空")
        if len(evidence.check_item_id) > 64:
            raise JudgeRequestError("check_item_id 超过 64 字符")
        if not evidence.photo_object_key and not evidence.photo_url:
            raise JudgeRequestError("photo_object_key 与 photo_url 至少需一个")

    # -- 主入口 ----------------------------------------------------------------
    async def judge(self, evidence: RoomCheckEvidence) -> RoomCheckVerdict:
        self._validate(evidence)

        # 1. 幂等命中（rejudge 端点会先 invalidate 再调本方法，故正常请求
        #    在短窗内可重放——与 initial_review 的 replayed=true 等价）
        cached = self._cache.get(evidence.fingerprint)
        if cached is not None:
            return cached

        # 2. 三级判定链（try/except 集中处理技术失败 → 固定 fallback）
        try:
            verdict = await self._run_chain(evidence)
        except JudgeTechnicalError:
            verdict = self._fallback_verdict(evidence)
        # 缓存成功与 fallback（rejudge 可重跑同一 fingerprint）
        self._cache.put(evidence.fingerprint, verdict)

        # 3. 异步 dispatch callback（fire-and-forget）
        self._dispatch_callback_async(verdict)
        return verdict

    async def rejudge(self, evidence: RoomCheckEvidence) -> RoomCheckVerdict:
        """强制重跑（清缓存 + 异步 callback 仍触发）。"""
        self._validate(evidence)
        self._cache.invalidate(evidence.fingerprint)
        return await self.judge(evidence)

    # -- 判定链 ----------------------------------------------------------------
    async def _run_chain(self, evidence: RoomCheckEvidence) -> RoomCheckVerdict:
        raw_outputs: dict[str, str] = {}
        reasons: list[str] = []
        score = SCORE_BASE

        expected_match = _coerce_match_type(evidence.expected_match_type, evidence.room_type)
        # 一级 type-match
        type_prompt = self._prompt_builder.build_type_match_prompt(
            evidence, observed_meta=self._observed_meta
        )
        type_result = await self._invoke(evidence, type_prompt)
        raw_outputs["type_match"] = type_result.raw_text
        if not self._parse_type_match_ok(type_result):
            return self._build_verdict(
                evidence,
                score=0,
                reasons=(
                    f"照片主体与期望「{expected_match}」不符，建议重拍",
                ),
                raw_outputs=raw_outputs,
                model_name=type_result.model,
            )

        # 二级 content
        content_prompt = self._prompt_builder.build_content_prompt(evidence)
        content_result = await self._invoke(evidence, content_prompt)
        raw_outputs["content"] = content_result.raw_text
        content_match, matched, missing = self._parse_content(content_result)
        if not content_match:
            score -= SCORE_DEDUCT_CONTENT_MISMATCH
            reasons.append(
                f"内容不匹配：缺失关键物证 {list(missing) or '（未识别）'}"
            )
        elif missing:
            score -= SCORE_DEDUCT_KEYWORDS_MISSING
            reasons.append(f"部分关键物证缺失：{list(missing)}")

        # 三级 evidence
        evidence_prompt = self._prompt_builder.build_evidence_prompt(
            evidence,
            content_match=content_match,
            matched_keywords=matched,
            missing_keywords=missing,
        )
        evidence_result = await self._invoke(evidence, evidence_prompt)
        raw_outputs["evidence"] = evidence_result.raw_text
        evidence_strength, evidence_notes = self._parse_evidence(evidence_result)
        if evidence_strength in {"WEAK", "NONE"}:
            score -= SCORE_DEDUCT_EVIDENCE_VAGUE
            reasons.append(f"证据模糊（{evidence_strength}）：{evidence_notes}")
        elif evidence_strength == "MODERATE":
            score -= SCORE_DEDUCT_OTHER
            reasons.append(f"证据一般（MODERATE）：{evidence_notes}")
        else:  # STRONG
            if not reasons:
                reasons.append(
                    f"证据具体（STRONG）：{evidence_notes or '照片中可见全部期望物证'}"
                )

        score = max(0, score)  # 收尾不越界
        return self._build_verdict(
            evidence,
            score=score,
            reasons=tuple(reasons),
            raw_outputs=raw_outputs,
            model_name=evidence_result.model,
        )

    # -- 单阶段调用 + 容错 -------------------------------------------------------
    async def _invoke(self, evidence: RoomCheckEvidence, prompt: str) -> VisionCallResult:
        try:
            async with asyncio.timeout(self._stage_timeout):
                return await self._vision_client.complete_vision(
                    prompt, image_data_urls=(self._photo_data_url,)
                )
        except (ModelUnavailableError, ModelCallError, TimeoutError) as exc:
            raise JudgeTechnicalError(f"视觉判定阶段失败：{exc}") from exc

    @staticmethod
    def _parse_type_match_ok(result: VisionCallResult) -> bool:
        parsed = result.parsed
        if not isinstance(parsed, dict):
            return False
        if "matched" in parsed:
            return bool(parsed.get("matched"))
        # 兼容旧 2-stage schema（type_match 字段）
        if "type_match" in parsed:
            return bool(parsed.get("type_match"))
        return False

    @staticmethod
    def _parse_content(result: VisionCallResult) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        parsed = result.parsed
        if not isinstance(parsed, dict):
            return False, (), ()
        content_match = bool(parsed.get("content_match", False))
        matched = tuple(str(x) for x in parsed.get("matched_keywords", []) or [])
        missing = tuple(str(x) for x in parsed.get("missing_keywords", []) or [])
        return content_match, matched, missing

    @staticmethod
    def _parse_evidence(result: VisionCallResult) -> tuple[str, str]:
        parsed = result.parsed
        if not isinstance(parsed, dict):
            return "NONE", "evidence 阶段未能解析 JSON"
        strength = str(parsed.get("evidence_strength") or "NONE").upper()
        if strength not in {"STRONG", "MODERATE", "WEAK", "NONE"}:
            strength = "NONE"
        notes = str(parsed.get("evidence_notes") or "").strip()
        return strength, notes

    # -- 结果组装 --------------------------------------------------------------
    def _build_verdict(
        self,
        evidence: RoomCheckEvidence,
        *,
        score: int,
        reasons: tuple[str, ...],
        raw_outputs: dict[str, str],
        model_name: str,
    ) -> RoomCheckVerdict:
        return RoomCheckVerdict(
            fingerprint=evidence.fingerprint,
            overall=_derive_overall(score),
            score=int(score),
            reasons=reasons,
            judged_at=_now(),
            model_name=model_name,
            raw_output=dict(raw_outputs),
        )

    def _fallback_verdict(self, evidence: RoomCheckEvidence) -> RoomCheckVerdict:
        """技术失败 / 超时 fallback：PROBLEM/0 + 固定 reason（与 Java 契约一致）。"""
        return RoomCheckVerdict(
            fingerprint=evidence.fingerprint,
            overall=JudgeVerdict.PROBLEM,
            score=0,
            reasons=(FALLBACK_TIMEOUT_REASON,),
            judged_at=_now(),
            model_name="",
            raw_output={"error": FALLBACK_TIMEOUT_REASON},
        )

    # -- 异步 callback dispatch ------------------------------------------------
    def _dispatch_callback_async(self, verdict: RoomCheckVerdict) -> None:
        async def _runner() -> None:
            try:
                await self._callback_client.dispatch(verdict)
            except Exception as exc:  # noqa: BLE001 - callback 失败不影响主流程
                logger.warning(
                    "[B6 callback runner] dispatch exception fingerprint=%s err=%s",
                    verdict.fingerprint,
                    exc,
                )

        coro = _runner()
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            # 无事件循环（同步上下文）→ 退化为记录协程供测试 drain
            self._pending_callbacks.append(coro)
            return
        # 持有 task 引用防止 GC；异常由 _runner 捕获不影响 task 自身
        self._pending_callbacks.append(_AwaitableWrapper(task))


class _AwaitableWrapper:
    """把 ``asyncio.Task`` 适配成 awaitable（便于测试 drain）。

    initial_review conftest 的 ``drain(pending)`` 直接 await 每个元素；
    ``asyncio.Task`` 已经是 awaitable，但若任务已完成/被 GC 可能找不到——
    这里加一层 wrapper 显式引用，避免悬挂。
    """

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task

    def __await__(self):
        return self._task.__await__()


__all__ = [
    "B6_MODEL_VERSION",
    "CACHE_MAX_ENTRIES",
    "CACHE_TTL_SECONDS",
    "DEFAULT_STAGE_TIMEOUT_SECONDS",
    "FALLBACK_TIMEOUT_REASON",
    "JudgeRequestError",
    "JudgeTechnicalError",
    "PROMPT_VERSION_CONTENT",
    "PROMPT_VERSION_EVIDENCE",
    "PROMPT_VERSION_TYPE_MATCH",
    "RoomCheckB6JudgeService",
    "SCORE_BASE",
    "SCORE_DEDUCT_CONTENT_MISMATCH",
    "SCORE_DEDUCT_EVIDENCE_VAGUE",
    "SCORE_DEDUCT_KEYWORDS_MISSING",
    "SCORE_DEDUCT_OTHER",
    "SCORE_PARTIAL_THRESHOLD",
    "SCORE_PASS_THRESHOLD",
]