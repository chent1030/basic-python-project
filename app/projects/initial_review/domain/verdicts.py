"""检查结论共享类型与 A9 意见汇总（聚合）。

口径（波次 2 / PRD §28.4「部分检查缺失不得伪造通过」）：
- 任一检查项 verdict=FAIL → overall=PROBLEM；
- 无 FAIL 但存在 WARN 或未出具结论的项（verdict=SKIPPED：未装配/不可用/前置条件缺失/
  执行降级 DEGRADED）→ overall=PARTIAL（部分完成，不伪造通过）；
- 全部 PASS → overall=PASS；
- 全部 SKIPPED → overall=PARTIAL + 说明（没有任何可采信结论）。

技术失败（超时/异常/输出无效）映射为 verdict=SKIPPED + implementation_status=DEGRADED：
基础设施故障不冒充内容判定，不单独把 overall 推成 PROBLEM（任务级解读见设计文档）。

implementation_status 取值：
- IMPLEMENTED：检查真实执行并出具结论（确定性规则或模型判定）；
- DEGRADED：尝试执行但技术失败（超时/异常/输出两次无效）；
- SKIPPED：前置条件不满足或检查未装配/模型不可用，未尝试调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIPPED = "SKIPPED"

IMPLEMENTED = "IMPLEMENTED"
DEGRADED = "DEGRADED"
SKIPPED_STATUS = "SKIPPED"

#: verdict 严重度排序（SKIPPED 最轻：不产生内容结论，仅影响覆盖度）
_SEVERITY = {PASS: 0, SKIPPED: 1, WARN: 2, FAIL: 3}

#: 回调 wire 层字段名（TEXT_LENGTH/PUNCTUATION 既有约定：short/long 不带 _measure 后缀）
WIRE_FIELD_NAMES = {
    "reason": "reason",
    "short_term_measure": "short_term",
    "long_term_measure": "long_term",
}


@dataclass
class CheckOutcome:
    """单项检查结论（聚合与 C-02 items 的共同来源，单一事实源）。"""

    check_type: str
    verdict: str
    implementation_status: str = IMPLEMENTED
    field_name: str | None = None
    reason: str | None = None
    confidence: float | None = None
    problem_fragment: str | None = None
    text_length: int | None = None
    punctuation_count: int | None = None
    ratio_ok: bool | None = None
    evidence_refs: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None


def worst_verdict(verdicts: list[str], *, skip_is_neutral: bool = True) -> str:
    """取最重判定；skip_is_neutral 时 SKIPPED 不参与（全 SKIPPED → 返回 SKIPPED）。"""
    effective = [v for v in verdicts if v != SKIPPED] if skip_is_neutral else list(verdicts)
    if not effective:
        return SKIPPED
    return max(effective, key=lambda v: _SEVERITY.get(v, 0))


def collect_check_outcomes(
    text_checks: dict[str, Any] | None, model_checks: dict[str, Any] | None
) -> list[CheckOutcome]:
    """把 wave1 文本规则结果 + wave2 模型检查结果统一为 CheckOutcome 列表。"""
    outcomes: list[CheckOutcome] = []
    checks = text_checks or {}
    for domain_field, wire_field in WIRE_FIELD_NAMES.items():
        # 序列化 text_rules 用 wire 键（short_term）；领域侧偶用全名 —— 两键兼容
        fc = checks.get(domain_field) or checks.get(wire_field) or {}
        outcomes.append(
            CheckOutcome(
                check_type="TEXT_LENGTH",
                field_name=wire_field,
                verdict=PASS if fc.get("length_ok") else FAIL,
                reason=None if fc.get("length_ok") else "文本长度不足 15 字（剔除换行后）",
                text_length=fc.get("length"),
            )
        )
        outcomes.append(
            CheckOutcome(
                check_type="PUNCTUATION_RATIO",
                field_name=wire_field,
                verdict=PASS if fc.get("ratio_ok") else FAIL,
                text_length=fc.get("length"),
                punctuation_count=fc.get("punctuation"),
                ratio_ok=fc.get("ratio_ok"),
                reason=(
                    None
                    if fc.get("ratio_ok")
                    else f"标点比例超限：P={fc.get('punctuation')}，L={fc.get('length')}"
                ),
            )
        )
        for v in fc.get("violations") or []:
            if v.get("type") == "consecutive_punctuation":
                outcomes.append(
                    CheckOutcome(
                        check_type="PUNCTUATION_RATIO",
                        field_name=wire_field,
                        verdict=FAIL,
                        text_length=fc.get("length"),
                        punctuation_count=fc.get("punctuation"),
                        ratio_ok=fc.get("ratio_ok"),
                        reason="存在连续标点",
                        problem_fragment=v.get("fragment"),
                    )
                )
    outcomes.extend(_model_check_outcomes(model_checks))
    return outcomes


def _model_check_outcomes(model_checks: dict[str, Any] | None) -> list[CheckOutcome]:
    mc = model_checks or {}
    outcomes: list[CheckOutcome] = []

    tv = mc.get("text_validity") or {}
    for domain_field, wire_field in WIRE_FIELD_NAMES.items():
        entry = (tv.get("fields") or {}).get(domain_field) or {}
        outcomes.append(
            CheckOutcome(
                check_type="TEXT_VALIDITY",
                field_name=wire_field,
                verdict=entry.get("verdict") or SKIPPED,
                implementation_status=entry.get("status") or SKIPPED_STATUS,
                reason=entry.get("reason"),
                confidence=entry.get("confidence"),
                problem_fragment=entry.get("problem_fragment"),
                model=tv.get("model"),
                prompt_version=tv.get("prompt_version"),
            )
        )

    ic = mc.get("image_compare") or {}
    readings = ic.get("readings") or {}
    reason = ic.get("reason")
    if ic.get("implementation_status") == IMPLEMENTED and readings.get("provided"):
        # 读数核对结论并入理由呈现（不单独成项：Java 契约仅五类 check_type）
        reading_note = readings.get("note") or ""
        if reading_note:
            reason = f"{reason or ''}；读数核对：{reading_note}"
    outcomes.append(
        CheckOutcome(
            check_type="IMAGE_COMPARE",
            verdict=ic.get("verdict") or SKIPPED,
            implementation_status=ic.get("implementation_status") or SKIPPED_STATUS,
            reason=reason,
            confidence=ic.get("confidence"),
            evidence_refs=list(ic.get("evidence_refs") or []),
            model=ic.get("model"),
            prompt_version=ic.get("prompt_version"),
        )
    )

    ms = mc.get("measure_similarity") or {}
    outcomes.append(
        CheckOutcome(
            check_type="MEASURE_SIMILARITY",
            verdict=ms.get("verdict") or SKIPPED,
            implementation_status=ms.get("implementation_status") or SKIPPED_STATUS,
            reason=ms.get("reason"),
            model=ms.get("rule_version"),
            prompt_version=ms.get("rule_version"),
        )
    )
    return outcomes


def aggregate_overall(
    text_checks: dict[str, Any] | None, model_checks: dict[str, Any] | None
) -> tuple[str, str | None]:
    """A9 意见汇总 → (overall, 说明)。overall ∈ PASS/PARTIAL/PROBLEM。"""
    outcomes = collect_check_outcomes(text_checks, model_checks)
    verdicts = [o.verdict for o in outcomes]
    if not verdicts:  # 防御：文本规则恒产出 6+ 项，理论不可达
        return "PARTIAL", "无任何检查结论"
    if FAIL in verdicts:
        return "PROBLEM", None
    if all(v == SKIPPED for v in verdicts):
        return (
            "PARTIAL",
            "全部检查项均未出具结论（SKIPPED）：" + "；".join(
                f"{o.check_type}/{o.field_name or '-'}:{o.reason or '无原因'}"
                for o in outcomes
            ),
        )
    if WARN in verdicts or SKIPPED in verdicts:
        skipped = [o for o in outcomes if o.verdict == SKIPPED]
        warned = [o for o in outcomes if o.verdict == WARN]
        parts: list[str] = []
        if warned:
            parts.append(
                "存在 WARN 项："
                + "、".join(f"{o.check_type}/{o.field_name or '-'}" for o in warned)
            )
        if skipped:
            parts.append(
                "未出具结论的项："
                + "、".join(f"{o.check_type}/{o.field_name or '-'}" for o in skipped)
            )
        return "PARTIAL", "；".join(parts)
    return "PASS", None


def composite_model_version(model_checks: dict[str, Any] | None) -> str:
    """按实际执行的检查组装 model_version（String(128) 内）。"""
    from app.projects.initial_review.domain.measure_similarity import (
        MEASURE_SIMILARITY_RULE_VERSION,
    )
    from app.projects.initial_review.domain.text_rules import TEXT_RULES_MODEL_VERSION

    parts = [TEXT_RULES_MODEL_VERSION, MEASURE_SIMILARITY_RULE_VERSION]
    mc = model_checks or {}
    tv = mc.get("text_validity") or {}
    if tv.get("implementation_status") == IMPLEMENTED:
        # 实际生效的 prompt 版本（infrastructure/prompts.py 常量随 model_checks 传入）
        parts.append(tv.get("prompt_version") or "text-validity@1")
    ic = mc.get("image_compare") or {}
    if ic.get("implementation_status") == IMPLEMENTED:
        parts.append(ic.get("prompt_version") or "image-compare@1")
    return "+".join(parts)


__all__ = [
    "CheckOutcome",
    "IMPLEMENTED",
    "DEGRADED",
    "FAIL",
    "PASS",
    "SKIPPED",
    "SKIPPED_STATUS",
    "WARN",
    "aggregate_overall",
    "collect_check_outcomes",
    "composite_model_version",
    "worst_verdict",
]
