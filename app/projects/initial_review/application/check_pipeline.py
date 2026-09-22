"""波次 2 检查管线：A6 雷同（确定性）+ A7 视觉比对 + A8 语义有效性 → model_checks。

编排骨架（设计 §3.1）：
- A6 ``check_measure_similarity``：纯本地确定性计算，恒执行（同单互比 PRD 强制；
  历史比对无历史 → SKIPPED+原因，不伪造）；
- A8 三字段循环：逐字段调用文本模型，单次预算 ``field_budget_seconds``
  （``asyncio.timeout``，可配，默认 90s）；空字段（规则层已 FAIL）不浪费调用；
- A7 附件双模式：object_key → RustFS（S3 GET）；content_base64 → 内存原文
  （仅首次执行可得；重放执行拿不到原文 → 如实 SKIPPED，不伪造）；
  读数核对并入同一次视觉调用；
- 模型不可用（ModelUnavailableError：未配置/禁用/客户端未装配）→ SKIPPED+原因；
  技术失败（ModelCallError/超时/输出两次无效）→ DEGRADED（不冒充内容结论）。

依赖注入：单测注入 FakeModelCheckClient / httpx.MockTransport；
真实模型冒烟走 scripts/wave2_real_model_smoke.py（不进 pytest）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.projects.initial_review.domain.image_compare import ImageCompareResult
from app.projects.initial_review.domain.measure_similarity import check_measure_similarity
from app.projects.initial_review.domain.text_validity import (
    FIELD_ORDER,
    FieldValidity,
    degraded_field_result,
    model_field_result,
    skipped_field_result,
)
from app.projects.initial_review.domain.verdicts import (
    DEGRADED,
    IMPLEMENTED,
    SKIPPED,
    SKIPPED_STATUS,
    worst_verdict,
)
from app.projects.initial_review.infrastructure.config import InitialReviewSettings
from app.projects.initial_review.infrastructure.model_client import (
    MIME_BY_SUFFIX,
    ModelCallError,
    ModelUnavailableError,
)
from app.projects.initial_review.infrastructure.prompts import (
    IMAGE_COMPARE_PROMPT_VERSION,
    TEXT_VALIDITY_PROMPT_VERSION,
    build_image_compare_prompt,
    build_issue_context,
    build_text_validity_prompt,
    extract_submitted_readings,
)

logger = logging.getLogger(__name__)

#: 历史提交条目的三字段键（Java issue_snapshot.history_submissions / DB input_snapshot 同构）
HISTORY_FIELD_KEYS = ("reason", "short_term_measure", "long_term_measure")

#: 历史提交装载器：(issue_id, version_no) → 早期已完成版本的 [{三字段+version_no}]
HistoryLoader = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


def normalize_history_item(item: Any) -> dict[str, Any] | None:
    """容错归一化一条历史提交（非 dict/全空 → None）。"""
    if not isinstance(item, dict):
        return None
    texts = {k: str(item.get(k) or "").strip() for k in HISTORY_FIELD_KEYS}
    if not any(texts.values()):
        return None
    out: dict[str, Any] = dict(texts)
    if item.get("version_no") is not None:
        out["version_no"] = item["version_no"]
    return out


def _dedupe_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按（version_no + 三字段内容）去重，保序（Java 快照优先，DB 补充）。"""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (
            item.get("version_no"),
            item["reason"],
            item["short_term_measure"],
            item["long_term_measure"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class CheckPipeline:
    """A6/A7/A8 检查编排（输出直接作为 model_checks 落库/回调）。"""

    def __init__(
        self,
        settings: InitialReviewSettings,
        *,
        model_client: Any | None = None,
        rustfs: Any | None = None,
        history_loader: HistoryLoader | None = None,
    ) -> None:
        self._settings = settings
        self._model = model_client
        self._rustfs = rustfs
        self._history_loader = history_loader

    # ------------------------------------------------------------------ 入口

    async def run(
        self, snapshot: dict[str, Any], request: Any | None
    ) -> dict[str, Any]:
        """执行三项模型/确定性检查 → model_checks dict（verdicts.collect 的输入形态）。"""
        texts = {
            "reason": snapshot.get("reason") or "",
            "short_term_measure": snapshot.get("short_term_measure") or "",
            "long_term_measure": snapshot.get("long_term_measure") or "",
        }
        issue_snapshot = snapshot.get("issue_snapshot") or {}
        issue_context = build_issue_context(issue_snapshot)

        similarity = await self._measure_similarity(
            texts,
            issue_id=str(snapshot.get("issue_id") or ""),
            version_no=int(snapshot.get("version_no") or 0),
            issue_snapshot=issue_snapshot,
        )
        text_validity = await self._text_validity(texts, issue_context)
        image_compare = await self._image_compare(snapshot, request, issue_context)
        return {
            "measure_similarity": similarity,
            "text_validity": text_validity,
            "image_compare": image_compare,
        }

    # ------------------------------------------------------------- A6 雷同性

    async def _measure_similarity(
        self,
        texts: dict[str, str],
        *,
        issue_id: str,
        version_no: int,
        issue_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        thresholds = self._settings.similarity
        history = await self._load_history(issue_id, version_no, issue_snapshot)
        return check_measure_similarity(
            reason=texts["reason"],
            short_term_measure=texts["short_term_measure"],
            long_term_measure=texts["long_term_measure"],
            history_submissions=history,
            same_fail_threshold=thresholds.same_fail,
            same_warn_threshold=thresholds.same_warn,
            history_fail_threshold=thresholds.history_fail,
            history_warn_threshold=thresholds.history_warn,
        )

    async def _load_history(
        self, issue_id: str, version_no: int, issue_snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """历史来源合并：issue_snapshot.history_submissions（Java 侧提供）
        + 本库 COMPLETED 早期版本。"""
        snapshot_history: list[dict[str, Any]] = list(
            issue_snapshot.get("history_submissions") or []
        )
        if self._history_loader is not None:
            try:
                db_items = list(await self._history_loader(issue_id, version_no))
            except Exception as exc:  # noqa: BLE001 - 历史装载失败不阻断主检查
                logger.warning("history loader failed: %s", exc)
                db_items = []
        else:
            db_items = []
        return _dedupe_history(
            [
                item
                for item in (
                    normalize_history_item(i) for i in snapshot_history + db_items
                )
                if item
            ]
        )

    # --------------------------------------------------------- A8 语义有效性

    async def _text_validity(self, texts: dict[str, str], issue_context: str) -> dict[str, Any]:
        budget = self._settings.model_check.field_budget_seconds
        fields: dict[str, dict[str, Any]] = {}
        for field in FIELD_ORDER:
            text = (texts.get(field) or "").strip()
            if not text:
                fields[field] = skipped_field_result(
                    field, "字段为空（文本长度规则已判 FAIL），语义检查无输入，跳过"
                )
                continue
            if self._model is None:
                fields[field] = skipped_field_result(
                    field, "模型检查客户端未装配（单测/降级部署），未出具结论"
                )
                continue
            prompt = build_text_validity_prompt(
                field_name=field, text=text, issue_context=issue_context
            )
            try:
                async with asyncio.timeout(budget):
                    result = await self._model.complete_text_json(prompt, FieldValidity)
                fields[field] = model_field_result(field, result)
            except ModelUnavailableError as exc:
                fields[field] = skipped_field_result(field, f"模型不可用，未出具结论：{exc}")
            except (ModelCallError, TimeoutError) as exc:
                kind = "单字段预算超时" if isinstance(exc, TimeoutError) else type(exc).__name__
                fields[field] = degraded_field_result(field, f"{kind}: {exc}")
        statuses = {v["status"] for v in fields.values()}
        if DEGRADED in statuses:
            implementation = DEGRADED
        elif IMPLEMENTED in statuses:
            implementation = IMPLEMENTED
        else:
            implementation = SKIPPED_STATUS
        return {
            "implementation_status": implementation,
            "model": self._settings.model_check.text_model,
            "prompt_version": TEXT_VALIDITY_PROMPT_VERSION,
            "fields": fields,
        }

    # ----------------------------------------------------------- A7 视觉比对

    async def _image_compare(
        self,
        snapshot: dict[str, Any],
        request: Any | None,
        issue_context: str,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "implementation_status": SKIPPED_STATUS,
            "model": self._settings.model_check.vision_model,
            "prompt_version": IMAGE_COMPARE_PROMPT_VERSION,
            "verdict": SKIPPED,
            "reason": None,
            "confidence": None,
            "evidence_refs": [],
            "readings": {"provided": False, "match": None, "observed": None, "note": None},
        }
        before = list(snapshot.get("before_attachments") or [])
        after = list(snapshot.get("after_attachments") or [])
        if not before and not after:
            base["reason"] = "未提交整改前后照片，无法比对"
            return base
        if not before or not after:
            base["reason"] = "缺少整改前/后照片其中一侧，无法比对"
            return base
        if self._model is None:
            base["reason"] = "模型检查客户端未装配（单测/降级部署），未出具结论"
            return base

        cap = self._settings.model_check.max_images_per_side
        truncated = len(before) > cap or len(after) > cap
        before, after = before[:cap], after[:cap]

        try:
            before_urls, before_refs = await self._resolve_side(
                before, getattr(request, "before_attachments", None)
            )
            after_urls, after_refs = await self._resolve_side(
                after, getattr(request, "after_attachments", None)
            )
        except ModelUnavailableError as exc:
            base["reason"] = f"附件不可用，未出具结论：{exc}"
            return base
        except (ModelCallError, TimeoutError) as exc:
            base["implementation_status"] = DEGRADED
            base["reason"] = f"附件获取失败，未出具结论（不伪造）：{exc}"
            return base

        issue_snapshot = snapshot.get("issue_snapshot") or {}
        readings = extract_submitted_readings(issue_snapshot)
        prompt = build_image_compare_prompt(
            before_count=len(before_urls),
            after_count=len(after_urls),
            issue_context=issue_context,
            submitted_readings=readings,
        )
        try:
            async with asyncio.timeout(self._settings.model_check.field_budget_seconds):
                result = await self._model.complete_vision_json(
                    prompt, before_urls + after_urls, ImageCompareResult
                )
        except ModelUnavailableError as exc:
            base["reason"] = f"模型不可用，未出具结论：{exc}"
            return base
        except (ModelCallError, TimeoutError) as exc:
            base["implementation_status"] = DEGRADED
            base["reason"] = f"视觉比对执行失败，未出具结论（不伪造）：{exc}"
            return base

        verdict = result.verdict
        reading_section = self._readings_section(readings, result)
        if reading_section["match"] is False and verdict != "FAIL":
            # 读数不一致是「照片与登记值矛盾」的实质信号：不弱于 WARN（OCR 不确定性下不直接 FAIL）
            verdict = worst_verdict([verdict, "WARN"])
        note_bits = []
        if truncated:
            note_bits.append(f"每侧仅取前 {cap} 张照片")
        if reading_section["note"]:
            note_bits.append(reading_section["note"])
        return {
            "implementation_status": IMPLEMENTED,
            "model": self._settings.model_check.vision_model,
            "prompt_version": IMAGE_COMPARE_PROMPT_VERSION,
            "verdict": verdict,
            "reason": result.reason + (f"（{('；'.join(note_bits))}）" if note_bits else ""),
            "confidence": round(result.confidence, 4),
            "evidence_refs": before_refs + after_refs,
            "evidence_before": list(result.evidence_before),
            "evidence_after": list(result.evidence_after),
            "readings": reading_section,
        }

    async def _resolve_side(
        self, metas: list[dict[str, Any]], request_attachments: Any | None
    ) -> tuple[list[str], list[str]]:
        """一侧附件元数据 → (data_urls, evidence_refs)。

        - object_key → RustFS 拉取（配置缺失 → ModelUnavailableError → SKIPPED）；
        - content_base64 → 仅内存原文可得（首次执行）；重放执行拿不到 → 技术失败 DEGRADED。
        """
        originals = list(request_attachments) if request_attachments is not None else []
        urls: list[str] = []
        refs: list[str] = []
        for idx, meta in enumerate(metas):
            if meta.get("object_key"):
                key = meta["object_key"]
                assert self._rustfs is not None  # object_key 模式必配 fetcher（装配层契约）
                urls.append(await self._rustfs.fetch_data_url(key))
                refs.append(key)
                continue
            digest = meta.get("content_base64_sha256")
            original = originals[idx] if idx < len(originals) else None
            content = getattr(original, "content_base64", None)
            if not content:
                raise ModelCallError(
                    f"base64 附件原文不可得（sha256={str(digest)[:12]}…，"
                    "重放执行无原文）：该照片无法送检"
                )
            file_name = meta.get("file_name") or getattr(original, "file_name", None) or ""
            suffix = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""
            mime = MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
            urls.append(f"data:{mime};base64,{content}")
            refs.append(
                meta.get("attachment_id") or f"base64:{str(digest)[:16]}"
            )
        return urls, refs

    @staticmethod
    def _readings_section(
        submitted: list[dict], result: ImageCompareResult
    ) -> dict[str, Any]:
        if not submitted:
            return {"provided": False, "match": None, "observed": None, "note": None}
        expected = "；".join(
            f"{r.get('name', '读数')}={r.get('value', '')}" for r in submitted
        )
        observed = result.readings_observed
        if result.readings_match is None:
            note = f"未能核对读数（登记：{expected}；观察：{observed or '未说明'}）"
        elif result.readings_match:
            note = f"读数与登记一致（{observed or expected}）"
        else:
            note = f"读数与登记不一致：登记[{expected}]，照片观察[{observed or '未说明'}]"
        return {
            "provided": True,
            "match": result.readings_match,
            "observed": observed,
            "note": note,
        }


__all__ = ["CheckPipeline", "HistoryLoader", "normalize_history_item"]
