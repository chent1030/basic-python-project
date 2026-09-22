"""A6 雷同性检查（确定性，无模型调用）——ADR-004「确定性规则用代码」。

两个子方面（口径见 docs/波次2-Python侧-A6A7A8A9设计.md §2.3，PRD §29.1 + 波次2 任务书）：
1. 同单互比（PRD AC-01/AC-16 强制）：short_term_measure ↔ long_term_measure；
2. 历史比对（波次2 任务书扩展，PRD §19.1 原范围为同单互比——已在设计文档标注待确认）：
   三字段 vs 同 issue 历史版本（自有 exec 表）与 issue_snapshot 携带的历史提交。

相似度 = max(SequenceMatcher.ratio, 字符二元组 Jaccard)：
- ratio 捕捉「整体顺序近似」的复制粘贴；
- bigram Jaccard 捕捉「局部重排/穿插」的复制粘贴。
阈值可配置（D-05：W3 前用业务样本校准；R-03：不许虚设——默认值来自人工抽检估计，待校准）。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from app.projects.initial_review.domain.verdicts import (
    FAIL,
    PASS,
    SKIPPED,
    SKIPPED_STATUS,
    WARN,
    worst_verdict,
)

#: 确定性规则版本（无 prompt，规则本身即版本锚点）
MEASURE_SIMILARITY_RULE_VERSION = "measure-similarity/det@1"

FIELDS = ("reason", "short_term_measure", "long_term_measure")


def _normalize(text: str) -> str:
    """去空白（空格/换行/制表）后比较：排版差异不算内容差异。"""
    return "".join(text.split())


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def similarity_score(a: str, b: str) -> float:
    """字符级相似度 ∈ [0,1]；两文本归一化后均为空 → 0（空文本由 TEXT_LENGTH 管）。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ba, bb = _bigrams(na), _bigrams(nb)
    union = ba | bb
    jaccard = len(ba & bb) / len(union) if union else 0.0
    return max(ratio, jaccard)


def _verdict_by_thresholds(score: float, fail_at: float, warn_at: float) -> str:
    if score >= fail_at:
        return FAIL
    if score >= warn_at:
        return WARN
    return PASS


def check_measure_similarity(
    *,
    reason: str,
    short_term_measure: str,
    long_term_measure: str,
    history_submissions: list[dict[str, Any]] | None,
    same_fail_threshold: float,
    same_warn_threshold: float,
    history_fail_threshold: float,
    history_warn_threshold: float,
) -> dict[str, Any]:
    """返回 MEASURE_SIMILARITY 检查结果（model_checks.measure_similarity 节）。"""
    texts = {
        "reason": reason,
        "short_term_measure": short_term_measure,
        "long_term_measure": long_term_measure,
    }
    # ---- 子方面 1：同单互比（PRD 强制，恒执行）----
    same_score = similarity_score(texts["short_term_measure"], texts["long_term_measure"])
    same_verdict = _verdict_by_thresholds(same_score, same_fail_threshold, same_warn_threshold)

    # ---- 子方面 2：历史比对（任务书扩展；无历史数据 → SKIPPED+原因，不伪造）----
    history = _check_history(
        texts, history_submissions or [], history_fail_threshold, history_warn_threshold
    )

    verdict = worst_verdict([same_verdict, history["verdict"]])
    reason_parts = [
        f"同单短/长期措施相似度 {same_score:.2f}（{same_verdict}，阈值 "
        f"FAIL≥{same_fail_threshold:.2f}/WARN≥{same_warn_threshold:.2f}）"
    ]
    if history["verdict"] == SKIPPED:
        reason_parts.append(f"历史比对：{history['reason']}")
    else:
        reason_parts.append(
            f"历史比对：{history['samples']} 个历史版本最大相似度 "
            f"{history['max_similarity']:.2f}（{history['verdict']}，阈值 "
            f"FAIL≥{history_fail_threshold:.2f}/WARN≥{history_warn_threshold:.2f}）"
            + (f"，命中 {history['hit']}" if history.get("hit") else "")
        )
    if verdict == FAIL:
        lead = "措施/文本疑似雷同："
    elif verdict == WARN:
        lead = "措施/文本相似度偏高，建议人工复核："
    else:
        lead = "未见明显雷同："
    return {
        "implementation_status": "IMPLEMENTED",
        "rule_version": MEASURE_SIMILARITY_RULE_VERSION,
        "verdict": verdict,
        "reason": lead + "；".join(reason_parts),
        "same_submission": {
            "similarity": round(same_score, 4),
            "verdict": same_verdict,
            "fields": ("short_term_measure", "long_term_measure"),
        },
        "history": history,
        "error": None,
    }


def _check_history(
    texts: dict[str, str],
    history_submissions: list[dict[str, Any]],
    fail_at: float,
    warn_at: float,
) -> dict[str, Any]:
    """历史子方面：当前每字段 vs 每个历史版本的全部三字段（防调换字段重提）。"""
    usable: list[dict[str, str]] = []
    for entry in history_submissions:
        if not isinstance(entry, dict):
            continue
        extracted = {f: str(entry.get(f) or "") for f in FIELDS}
        if any(extracted.values()):
            usable.append(extracted)
    if not usable:
        return {
            "verdict": SKIPPED,
            "status": SKIPPED_STATUS,
            "reason": "无历史提交可比对（无既往版本且 issue_snapshot 未携带历史）",
            "samples": 0,
            "max_similarity": None,
        }

    best_score, best_hit = 0.0, ""
    for idx, hist in enumerate(usable):
        for cur_field, cur_text in texts.items():
            if not _normalize(cur_text):
                continue
            for hist_field, hist_text in hist.items():
                if not _normalize(hist_text):
                    continue
                score = similarity_score(cur_text, hist_text)
                if score > best_score:
                    best_score = score
                    best_hit = f"第{idx + 1}条历史 {hist_field} ↔ 当前 {cur_field}"
    verdict = _verdict_by_thresholds(best_score, fail_at, warn_at)
    return {
        "verdict": verdict,
        "status": "IMPLEMENTED",
        "samples": len(usable),
        "max_similarity": round(best_score, 4),
        "hit": best_hit,
        "reason": None,
    }


__all__ = [
    "MEASURE_SIMILARITY_RULE_VERSION",
    "check_measure_similarity",
    "similarity_score",
]
