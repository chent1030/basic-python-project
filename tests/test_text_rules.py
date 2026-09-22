r"""线 A5 check_text_rules 穷举边界单测（D-22 冻结口径 + M0 用户裁决）。

覆盖清单（任务书 + PRD §29.2 示例）：
- L=15,P=1 ✓；L=15,P=2 ✗（比例）；L=20,P=2 ✓（恰 10%，整数边界）；
  L=19,P=2 ✗（证明整数精确比较，无浮点）；L=14,P=0 ✗；空文本 ✗（无异常）；
- ……（U+2026）：首/中/尾、奇数个、四个连打、单个、成对不计连续；
- 连续标点：『，。』、『， 。』（空格分隔仍判连续）、全角空格同口径、『，……』严格判连续；
- 空格计入 L 与 P（半角 U+0020 与全角 U+3000）；\t 计 L 不计 P（冻结表显式声明）；
- 换行剔除：\n \r U+2028 U+2029 不计 L 不计 P；
- 三字段独立：一字段违规不影响其他字段；
- 结果类型均为 int（无浮点参与）。

注意：源码中不可见字符一律用 \uXXXX 转义，禁止字面量（历史上曾因传输损坏踩坑）。
"""

from __future__ import annotations

from app.projects.initial_review.domain.text_rules import (
    ELLIPSIS_CHAR,
    check_rectification_texts,
    check_text_rules,
)

ZH = "整"  # 任意非标点汉字


def n(n_: int) -> str:
    return ZH * n_


# ------------------------------------------------ 基础边界（PRD §29.2 示例）----


def test_l15_p1_valid() -> None:
    r = check_text_rules(n(14) + "，")
    assert (r.length, r.punctuation) == (15, 1)
    assert r.valid and r.length_ok and r.ratio_ok and r.no_consecutive_punct
    assert r.violations == ()


def test_l15_p2_ratio_fails() -> None:
    # 7字+，+6字+。 = 15 字 2 标点，两标点不相邻 → 只有比例违规
    r = check_text_rules(n(7) + "，" + n(6) + "。")
    assert (r.length, r.punctuation) == (15, 2)
    assert not r.ratio_ok
    assert r.length_ok and r.no_consecutive_punct
    assert not r.valid
    assert [v.type for v in r.violations] == ["punctuation_ratio_exceeded"]


def test_l20_p2_exact_10pct_valid() -> None:
    # 10*2=20 <= 20：恰 10% 满足（整数比较边界）
    r = check_text_rules(n(9) + "，" + n(9) + "。")
    assert (r.length, r.punctuation) == (20, 2)
    assert r.ratio_ok and r.valid


def test_l19_p2_fails_proves_integer_comparison() -> None:
    # 10*2=20 > 19：若用浮点 2/19≈0.105>0.1 同样失败，但 2/20 恰等情形
    # （上一用例）只有整数比较能稳定放行 —— 此用例守住整数口径不被浮点替换。
    r = check_text_rules(n(9) + "，" + n(8) + "。")
    assert (r.length, r.punctuation) == (19, 2)
    assert not r.ratio_ok and not r.valid


def test_l14_p0_length_fails() -> None:
    r = check_text_rules(n(14))
    assert (r.length, r.punctuation) == (14, 0)
    assert not r.length_ok and r.ratio_ok and r.no_consecutive_punct
    assert not r.valid
    assert [v.type for v in r.violations] == ["length_below_minimum"]


def test_empty_text_invalid_and_safe() -> None:
    r = check_text_rules("")
    assert (r.length, r.punctuation) == (0, 0)
    assert not r.valid
    assert [v.type for v in r.violations] == ["length_below_minimum"]
    assert "文本为空" in r.violations[0].message


def test_results_are_int_no_floats() -> None:
    r = check_text_rules(n(14) + "，")
    assert isinstance(r.length, int) and isinstance(r.punctuation, int)
    assert all(isinstance(x, bool) for x in (r.length_ok, r.ratio_ok, r.no_consecutive_punct))


# ------------------------------------------------------------ ……（U+2026）----


def test_ellipsis_pair_at_start() -> None:
    # “……”在 L 计 2、在 P 计 1（M0 用户裁决）
    r = check_text_rules(ELLIPSIS_CHAR * 2 + n(14))
    assert (r.length, r.punctuation) == (16, 1)
    assert r.valid and r.no_consecutive_punct


def test_ellipsis_pair_in_middle() -> None:
    r = check_text_rules(n(7) + ELLIPSIS_CHAR * 2 + n(9))
    assert (r.length, r.punctuation) == (18, 1)
    assert r.valid and r.no_consecutive_punct


def test_ellipsis_pair_at_end() -> None:
    r = check_text_rules(n(15) + ELLIPSIS_CHAR * 2)
    assert (r.length, r.punctuation) == (17, 1)
    assert r.valid and r.no_consecutive_punct


def test_single_ellipsis_counts_one_punct() -> None:
    # 单个 U+2026 也计一标点（设计 §3.4:187）
    r = check_text_rules(n(7) + ELLIPSIS_CHAR + n(8))
    assert (r.length, r.punctuation) == (16, 1)
    assert r.valid


def test_odd_ellipsis_run_three_counts_two() -> None:
    # 奇数个 …（3 个）：成对消耗 1 + 孤余单字符 1 = P 2（歧义处取更严格解释）
    r = check_text_rules(n(8) + ELLIPSIS_CHAR * 3 + n(9))
    assert (r.length, r.punctuation) == (20, 2)
    assert r.punctuation == 2
    assert r.no_consecutive_punct, "…… 游程内部不判连续"
    assert r.valid


def test_four_ellipsis_counts_two() -> None:
    r = check_text_rules(ELLIPSIS_CHAR * 4 + n(16))
    assert (r.length, r.punctuation) == (20, 2)
    assert r.valid and r.no_consecutive_punct


def test_comma_adjacent_to_ellipsis_run_is_consecutive_strict() -> None:
    # 严格解释：『，……』—— ， 与 ……-游程 token 相邻 → 判连续（token 流=字符相邻读法）
    r = check_text_rules(n(7) + "，" + ELLIPSIS_CHAR * 2 + n(8))
    assert not r.no_consecutive_punct
    assert not r.valid
    consec = [v for v in r.violations if v.type == "consecutive_punctuation"]
    assert len(consec) == 1
    assert consec[0].fragment == "，……"


# ------------------------------------------------------------- 连续标点 ----


def test_adjacent_full_width_comma_period() -> None:
    r = check_text_rules(n(7) + "，。" + n(8))
    assert not r.no_consecutive_punct and not r.valid
    consec = [v for v in r.violations if v.type == "consecutive_punctuation"]
    assert len(consec) == 1 and consec[0].fragment == "，。"


def test_space_between_punct_still_consecutive() -> None:
    # PRD §29.2.6：空格属标点类，不可用空格绕过
    r = check_text_rules(n(7) + "， 。" + n(8))
    assert not r.no_consecutive_punct
    consec = [v for v in r.violations if v.type == "consecutive_punctuation"]
    assert len(consec) == 1 and consec[0].fragment == "， 。"


def test_full_width_space_between_punct_still_consecutive() -> None:
    r = check_text_rules(n(7) + "，\u3000。" + n(8))
    assert not r.no_consecutive_punct


def test_ascii_punct_consecutive() -> None:
    r = check_text_rules("A" * 7 + ",." + "B" * 8)
    assert not r.no_consecutive_punct


def test_long_punct_run_reports_single_violation() -> None:
    # 极大游程一条 violation，不按相邻对拆多条
    r = check_text_rules(n(7) + "，。；：" + n(8))
    consec = [v for v in r.violations if v.type == "consecutive_punctuation"]
    assert len(consec) == 1
    assert consec[0].fragment == "，。；："


# ------------------------------------------------ 空格/换行/制表符口径 ----


def test_half_width_space_counts_in_l_and_p() -> None:
    # 空格计入 L（长度）且计入 P（标点比例）—— PRD §29.2.2
    r = check_text_rules(n(5) + " " + n(9))
    assert (r.length, r.punctuation) == (15, 1)
    assert r.valid


def test_full_width_space_counts_in_l_and_p() -> None:
    # U+3000 全角空格：Phase 0 §⑥ 冻结同算（保守防绕过）
    r = check_text_rules(n(5) + "\u3000" + n(9))
    assert (r.length, r.punctuation) == (15, 1)
    assert r.valid


def test_newlines_stripped_not_counted() -> None:
    for nl in ("\n", "\r", "\u2028", "\u2029"):
        r = check_text_rules(n(7) + nl + n(8))
        assert (r.length, r.punctuation) == (15, 0), f"换行 {nl!r} 未剔除"
        assert r.valid


def test_mixed_newlines_stripped() -> None:
    r = check_text_rules("\n" + n(7) + "\r\n\u2028\u2029" + n(8) + "\u2029")
    assert (r.length, r.punctuation) == (15, 0)
    assert r.valid


def test_tab_counts_l_not_p() -> None:
    # \t 冻结口径：不剔除（计 L）、不计 P（显式声明，Phase 0 §⑥-5）
    r = check_text_rules(n(7) + "\t" + n(8))
    assert (r.length, r.punctuation) == (16, 0)
    assert r.valid


# ----------------------------------------------------------- 三字段独立 ----


def test_three_fields_independent() -> None:
    result = check_rectification_texts(
        reason=n(20),
        short_term_measure="",  # 违规：空
        long_term_measure="，。",  # 违规：连续标点 + 长度
    )
    assert result["reason"].valid
    assert not result["short_term"].valid
    assert not result["long_term"].valid
    # 各字段独立计算，互不污染
    assert result["reason"].length == 20
    assert result["short_term"].length == 0
    assert result["long_term"].length == 2
    assert set(result) == {"reason", "short_term", "long_term"}


def test_three_fields_same_content_same_results() -> None:
    text = n(7) + "，" + n(8)
    result = check_rectification_texts(
        reason=text, short_term_measure=text, long_term_measure=text
    )
    assert all(fc.valid for fc in result.values())
    assert all(fc.length == 16 and fc.punctuation == 1 for fc in result.values())


# ------------------------------------------------------------- 杂项口径 ----


def test_violation_carries_position_and_fragment() -> None:
    r = check_text_rules("，。" + n(20))
    consec = r.violations[0]
    assert consec.position == 0
    assert consec.fragment == "，。"


def test_typical_passing_text() -> None:
    # 43 字 4 标点：10×4=40 ≤ 43，通过
    text = "已完成设备更换并复测合格，运行恢复正常，现场已全部清理完毕，后续将按计划持续跟踪复查。"
    r = check_text_rules(text)
    assert (r.length, r.punctuation) == (43, 4)
    assert r.valid, r.violations


def test_multi_violation_combines() -> None:
    # 长度不足 + 比例超限 + 连续标点 三项并存
    r = check_text_rules("，。整")
    assert (r.length, r.punctuation) == (3, 2)
    assert not r.valid
    assert [v.type for v in r.violations] == [
        "length_below_minimum",
        "punctuation_ratio_exceeded",
        "consecutive_punctuation",
    ]
