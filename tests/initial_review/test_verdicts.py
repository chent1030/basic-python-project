"""A9 意见汇总（aggregate_overall）表驱动测试 + composite_model_version。"""

from __future__ import annotations

import pytest

from app.projects.initial_review.domain.verdicts import (
    aggregate_overall,
    composite_model_version,
    worst_verdict,
)

# ---------------------------------------------------------------- 纯 verdict 视角


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["PASS", "PASS"], "PASS"),
        (["PASS", "WARN"], "WARN"),
        (["WARN", "PASS"], "WARN"),
        (["PASS", "FAIL"], "FAIL"),
        (["FAIL", "WARN"], "FAIL"),
        (["SKIPPED", "PASS"], "PASS"),  # skip 中性
        (["SKIPPED"], "SKIPPED"),
        ([], "SKIPPED"),
    ],
)
def test_worst_verdict(verdicts: list[str], expected: str) -> None:
    assert worst_verdict(verdicts) == expected


# ------------------------------------------------------------- model_checks 构造


def tv_field(status: str, verdict: str) -> dict:
    return {"status": status, "verdict": verdict, "reason": "r", "field": "reason"}


def model_checks(
    *,
    tv_statuses: tuple[str, ...] = ("IMPLEMENTED", "IMPLEMENTED", "IMPLEMENTED"),
    tv_verdicts: tuple[str, ...] | None = None,
    ic_status: str = "IMPLEMENTED",
    ic_verdict: str = "PASS",
    ms_verdict: str = "PASS",
) -> dict:
    # 非实现状态默认 verdict=SKIPPED（对齐 text_validity.skipped/degraded_field_result）
    if tv_verdicts is None:
        tv_verdicts = tuple(
            "PASS" if st == "IMPLEMENTED" else "SKIPPED" for st in tv_statuses
        )
    fields = {}
    for name, status, verdict in zip(
        ("reason", "short_term_measure", "long_term_measure"),
        tv_statuses,
        tv_verdicts,
        strict=True,
    ):
        fields[name] = tv_field(status, verdict)
    return {
        "text_validity": {
            "implementation_status": (
                "IMPLEMENTED" if "IMPLEMENTED" in tv_statuses else tv_statuses[0]
            ),
            "prompt_version": "text-validity/qwen-plus@1",
            "fields": fields,
        },
        "image_compare": {
            "implementation_status": ic_status,
            "verdict": ic_verdict,
            "prompt_version": "image-compare/qwen-vl@1",
        },
        "measure_similarity": {
            "implementation_status": "IMPLEMENTED",
            "verdict": ms_verdict,
            "rule_version": "measure-similarity/det@1",
        },
    }


def text_checks_all(ok: bool = True) -> dict:
    fc = {
        "length": 20,
        "length_ok": ok,
        "punctuation": 0,
        "ratio_ok": ok,
        "violations": [],
    }
    return {"reason": dict(fc), "short_term": dict(fc), "long_term": dict(fc)}


# ------------------------------------------------------------------ 表驱动主体

CASES = [
    # (说明, text_ok, mc, expected_overall, note_expected)
    (
        "任一 FAIL → PROBLEM（文本规则 FAIL）",
        False,
        model_checks(),
        "PROBLEM",
        False,
    ),
    (
        "任一 FAIL → PROBLEM（模型 FAIL，即便其余全 PASS）",
        True,
        model_checks(tv_verdicts=("FAIL", "PASS", "PASS")),
        "PROBLEM",
        False,
    ),
    (
        "任一 FAIL → PROBLEM（视觉 FAIL）",
        True,
        model_checks(ic_verdict="FAIL"),
        "PROBLEM",
        False,
    ),
    (
        "任一 FAIL → PROBLEM（A6 雷同 FAIL）",
        True,
        model_checks(ms_verdict="FAIL"),
        "PROBLEM",
        False,
    ),
    (
        "有 WARN 无 FAIL → PARTIAL（附 WARN 说明）",
        True,
        model_checks(tv_verdicts=("WARN", "PASS", "PASS")),
        "PARTIAL",
        True,
    ),
    (
        "全 PASS → PASS",
        True,
        model_checks(),
        "PASS",
        False,
    ),
    (
        "混合：一项 SKIPPED 其余 PASS → PARTIAL（说明未出具结论项）",
        True,
        model_checks(tv_statuses=("SKIPPED", "IMPLEMENTED", "IMPLEMENTED")),
        "PARTIAL",
        True,
    ),
    (
        "技术降级 DEGRADED（verdict=SKIPPED）→ PARTIAL 不冒充内容结论",
        True,
        model_checks(tv_statuses=("DEGRADED", "IMPLEMENTED", "IMPLEMENTED")),
        "PARTIAL",
        True,
    ),
    (
        "全 SKIPPED（模型检查全不可用）→ PARTIAL + 逐项说明",
        True,
        model_checks(
            tv_statuses=("SKIPPED", "SKIPPED", "SKIPPED"),
            ic_status="SKIPPED",
            ic_verdict="SKIPPED",
        ),
        "PARTIAL",
        True,
    ),
]


@pytest.mark.parametrize(
    ("desc", "text_ok", "mc", "expected", "note_expected"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_aggregate_overall_mixed(desc, text_ok, mc, expected, note_expected) -> None:
    overall, note = aggregate_overall(text_checks_all(text_ok), mc)
    assert overall == expected, desc
    assert (note is not None) == note_expected, desc
    if note:
        assert isinstance(note, str) and note


def test_aggregate_overall_all_skipped_lists_each_reason() -> None:
    """全 SKIPPED：说明需逐项列出原因（可审计），不得只给一句空泛理由。"""
    mc = model_checks(
        tv_statuses=("SKIPPED", "SKIPPED", "SKIPPED"),
        ic_status="SKIPPED",
        ic_verdict="SKIPPED",
    )
    overall, note = aggregate_overall(text_checks_all(True), mc)
    assert overall == "PARTIAL"
    assert note and "TEXT_VALIDITY" in note and "IMAGE_COMPARE" in note


# ------------------------------------------------------------ composite 版本串


@pytest.mark.parametrize(
    ("mc", "expected"),
    [
        (
            model_checks(),
            "text-rules/d-22@1+measure-similarity/det@1"
            "+text-validity/qwen-plus@1+image-compare/qwen-vl@1",
        ),
        (
            model_checks(tv_statuses=("SKIPPED",) * 3, ic_status="SKIPPED"),
            "text-rules/d-22@1+measure-similarity/det@1",
        ),
        (
            model_checks(tv_statuses=("DEGRADED", "IMPLEMENTED", "SKIPPED")),
            "text-rules/d-22@1+measure-similarity/det@1"
            "+text-validity/qwen-plus@1+image-compare/qwen-vl@1",
        ),
        (None, "text-rules/d-22@1+measure-similarity/det@1"),
    ],
    ids=["全实现", "全跳过", "部分降级", "空"],
)
def test_composite_model_version(mc, expected) -> None:
    assert composite_model_version(mc) == expected


def test_composite_model_version_within_db_limit() -> None:
    """DB 列 String(128)：最长组合也不得超限。"""
    assert len(composite_model_version(model_checks())) <= 128
