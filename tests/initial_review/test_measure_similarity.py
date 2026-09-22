"""A6 措施/文本雷同性（measure_similarity）单测：阈值可配 + 历史比对语义。"""

from __future__ import annotations

import pytest

from app.projects.initial_review.domain.measure_similarity import (
    MEASURE_SIMILARITY_RULE_VERSION,
    check_measure_similarity,
    similarity_score,
)

SHORT = "已更换锈蚀接地扁铁并对接头做紧固和防腐涂刷处理"
LONG = "建立季度专项巡检制度对接地电阻值定期检测并纳入班组考核"
REASON = "设备接地引下线锈蚀严重导致接触不良存在安全隐患"

DEFAULTS = dict(
    same_fail_threshold=0.90,
    same_warn_threshold=0.75,
    history_fail_threshold=0.85,
    history_warn_threshold=0.70,
)


def run(short: str = SHORT, long: str = LONG, history=None, **over):
    kwargs = {**DEFAULTS, **over}
    return check_measure_similarity(
        reason=REASON,
        short_term_measure=short,
        long_term_measure=long,
        history_submissions=history,
        **kwargs,
    )


# ----------------------------------------------------------------- 相似度底层


@pytest.mark.parametrize(
    ("a", "b", "expected_range"),
    [
        ("", "", (0.0, 0.0)),  # 空文本无相似度可言
        ("完全相同的内容完全相同的内容", "完全相同的内容完全相同的内容", (0.99, 1.0)),
        (SHORT, LONG, (0.0, 0.3)),  # 异文低相似
        (SHORT, SHORT.replace("紧固", "加固"), (0.6, 0.99)),  # 微改高相似
    ],
)
def test_similarity_score_ranges(a, b, expected_range) -> None:
    score = similarity_score(a, b)
    assert expected_range[0] <= score <= expected_range[1], score


def test_similarity_ignores_whitespace() -> None:
    assert similarity_score("同 一 段 落", "同一段落") > 0.9


# ------------------------------------------------------------------- 同单互比


def test_same_submission_pass_when_distinct() -> None:
    result = run()
    assert result["verdict"] == "PASS"
    assert result["same_submission"]["similarity"] < 0.75
    assert result["implementation_status"] == "IMPLEMENTED"
    assert result["rule_version"] == MEASURE_SIMILARITY_RULE_VERSION


def test_same_submission_fail_when_identical() -> None:
    text = "临时采用螺栓紧固并做标记围挡防止人员靠近逗留避免扩大"
    result = run(short=text, long=text)
    assert result["same_submission"]["similarity"] == pytest.approx(1.0)
    assert result["verdict"] == "FAIL"
    assert "雷同" in result["reason"]


def test_same_submission_warn_band() -> None:
    """阈值可配：WARN 档（0.75≤s<0.90）随配置生效。"""
    base = "对锈蚀部位进行除锈刷漆并更换部分连接金具紧固螺栓"
    variant = base[:18] + "复紧全部螺栓"  # 部分重叠（实测 s≈0.875）
    score = similarity_score(base, variant)
    assert 0.75 <= score < 0.90, score  # 前置：样例确在 WARN 档
    result = run(short=base, long=variant)
    assert result["verdict"] == "WARN"


def test_thresholds_are_configurable() -> None:
    """同一文本对，抬阈值 FAIL→PASS：阈值不是硬编码。"""
    base = "对锈蚀部位进行除锈刷漆并更换部分连接金具紧固螺栓"
    variant = base[:18] + "复紧全部螺栓"  # 实测 s≈0.875：默认 WARN，抬阈值后 PASS
    loose = run(short=base, long=variant, same_fail_threshold=0.99, same_warn_threshold=0.95)
    assert loose["verdict"] == "PASS"


# ------------------------------------------------------------------- 历史比对


def test_history_skipped_without_history() -> None:
    result = run(history=None)
    assert result["history"]["verdict"] == "SKIPPED"
    assert "无历史" in result["history"]["reason"]
    assert "无历史提交可比对" in result["reason"]


def test_history_resubmission_same_texts_fails() -> None:
    """原样重提（vs 历史版本）→ FAIL。"""
    history = [
        {
            "reason": REASON,
            "short_term_measure": SHORT,
            "long_term_measure": LONG,
            "version_no": 1,
        }
    ]
    result = run(history=history)
    assert result["history"]["samples"] == 1
    assert result["history"]["verdict"] == "FAIL"
    assert result["verdict"] == "FAIL"


def test_history_field_swap_detected() -> None:
    """防调换重提：short/long 与历史版本互换仍命中（逐字段全组合比对）。"""
    history = [
        {
            "reason": REASON,
            "short_term_measure": LONG,   # 与本次提交对调
            "long_term_measure": SHORT,
            "version_no": 1,
        }
    ]
    result = run(history=history)
    assert result["history"]["verdict"] == "FAIL"


def test_history_distinct_submission_passes() -> None:
    history = [
        {
            "reason": "历史版本的原因说明与本次完全不同的表述",
            "short_term_measure": "历史短期措施更换绝缘子并试验合格后投运",
            "long_term_measure": "历史长期措施开展红外测温普查并更新台账",
            "version_no": 1,
        }
    ]
    result = run(history=history)
    assert result["history"]["verdict"] == "PASS"
    assert result["verdict"] == "PASS"


def test_history_max_similarity_across_versions() -> None:
    """多历史版本取最大相似度判定（最保守口径）。"""
    near_copy = SHORT[:10] + "以及同步开展同类隐患排查整改闭环"
    history = [
        {
            "reason": "第一版历史原因文字描述完全不同没有重叠",
            "short_term_measure": "第一版历史短期措施完全不同",
            "long_term_measure": "第一版历史长期措施完全不同",
            "version_no": 1,
        },
        {
            "reason": REASON,
            "short_term_measure": near_copy,
            "long_term_measure": LONG,
            "version_no": 2,
        },
    ]
    result = run(history=history)
    assert result["history"]["samples"] == 2
    assert result["history"]["verdict"] in ("WARN", "FAIL")
