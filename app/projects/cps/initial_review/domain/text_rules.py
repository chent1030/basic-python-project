"""D-22 冻结口径：文本规则引擎（线 A5，确定性检查）。

口径出处：
- PRD §29.2（八条口径）与 §29.1（五检查项中的文本三项）；
- 《后端架构与技术设计考虑》§3.4 check_text_rules 纯函数规格；
- Phase 0 前置核验报告（Python 侧）§⑥ 冻结表。

【M0 用户裁决：code point 口径（2025-12 波次 1）】
- 字数 L 按字符数（code point，即 Python ``len()``）计；
- “……”（两个 U+2026）在 L 计 2，在 P 计 1；
- 后端设计文档 §3.4:188 示例中的『6』系 UTF-8 字节读法笔误，以本裁决为准。

本模块为纯函数（无 IO、无状态），三字段（整改原因/短期措施/长期措施）分别独立计算，禁止合并。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 口径常量 ----

#: 换行剔除集合（PRD §29.2.1；设计 §3.4:186）。仅剔除这四种，不计 L、不计 P。
#: 注意：\t / \v / \f 不在本集合（Phase 0 §⑥ 冻结：不剔除、不计 P，显式声明）。
NEWLINE_CHARS: frozenset[str] = frozenset({"\n", "\r", "\u2028", "\u2029"})

#: 空格类：计入 L，且计入 P（PRD §29.2.2 “空格也作为标点计数”）。
#: - U+0020 半角空格：必算（PRD §29.2.2）；
#: - U+3000 全角空格：Phase 0 §⑥ 冻结为同算（保守防绕过）。
SPACE_CHARS: frozenset[str] = frozenset({" ", "　"})

#: 省略号字符 U+2026 “…”。状态机规则见 _tokenize。
ELLIPSIS_CHAR = "…"

#: 中文标点（设计 §3.4:187 明列 + Phase 0 §⑥-3 补充评审：· ～ 与 CJK 括引号族）。
CHINESE_PUNCTUATION: frozenset[str] = frozenset(
    "，、。；：？！·～—（）【】《》〈〉「」『』〔〕“”‘’"
)

#: 英文（半角）标点（设计 §3.4:187）。
ASCII_PUNCTUATION: frozenset[str] = frozenset(",.;:?!'\"()[]{}-")

#: 标点全集 = 中文 ∪ 英文。P = 出现次数（非种类数，PRD §29.2.7）。
PUNCTUATION_CHARS: frozenset[str] = CHINESE_PUNCTUATION | ASCII_PUNCTUATION

#: 标点/空格类全集：连续判定与 P 计数的并集口径（U+2026 单列因状态机特殊处理）。
PUNCT_OR_SPACE: frozenset[str] = PUNCTUATION_CHARS | SPACE_CHARS | {ELLIPSIS_CHAR}

#: 文本长度阈值：三字段各 ≥15 字（PRD §29.2.4；L≥15）。
MIN_LENGTH = 15

#: 标点比例上限：各字段独立 P ≤ L 的 10%（PRD §29.2.5/29.2.8）。
#: 用整数精确比较 ``10 * P <= L``（设计 §3.4:189），全程无浮点参与。
RATIO_NUMERATOR = 10

#: 规则集版本号（无模型参与，但规则集本身有版本，落 exec 记录 model_version 列）。
TEXT_RULES_MODEL_VERSION = "text-rules/d-22@1"


# ---------------------------------------------------------------- 结果结构 ----


@dataclass(frozen=True)
class Violation:
    """单条违规。position 为剔除换行后文本中的 code point 下标（0 起）。"""

    type: str  # length_below_minimum | punctuation_ratio_exceeded | consecutive_punctuation
    message: str
    position: int | None = None
    fragment: str | None = None


@dataclass(frozen=True)
class FieldTextCheck:
    """单字段的确定性文本检查结果（设计 §3.4:189 返回结构的实现形态）。"""

    length: int  # L：剔除换行后 code point 数（空格计入）
    punctuation: int  # P：标点+空格出现次数（…… 每对计 1）
    length_ok: bool  # L >= 15
    ratio_ok: bool  # 10 * P <= L（整数精确）
    no_consecutive_punct: bool  # 无相邻标点/空格类违规
    valid: bool  # = length_ok AND ratio_ok AND no_consecutive_punct
    violations: tuple[Violation, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "length": self.length,
            "punctuation": self.punctuation,
            "length_ok": self.length_ok,
            "ratio_ok": self.ratio_ok,
            "no_consecutive_punct": self.no_consecutive_punct,
            "valid": self.valid,
            "violations": [v.__dict__ for v in self.violations],
        }


# ---------------------------------------------------------------- 核心实现 ----


def _tokenize(s: str) -> list[tuple[bool, int, int]]:
    """把剔除换行后的文本切成 token 流：[(是否标点/空格类, 起, 止), ...]。

    U+2026 状态机（PRD §29.2.3 + M0 用户裁决）：
    - 极大游程（连续 k 个 U+2026）整体消耗为**单个** token：P += ceil(k/ 2)
      （每对计 1，孤余单字符按“单个 U+2026 也计一标点”计 1 —— 歧义处取更严格解释：
      奇数个 … 的孤余部分仍占一个 P，而不是被吞掉）；
    - 游程内部不判连续标点（成对消耗语义）；游程与其他标点/空格相邻仍判连续
      （严格解释：token 流语义与字符相邻读法一致，『，……』违规）。
    - 不得折叠其他任何重复标点（设计 §3.4:187）。

    空白字符 \t/\v/\f 按普通字符处理（不计 P，见 NEWLINE_CHARS 注释）。
    """
    tokens: list[tuple[bool, int, int]] = []
    punct_count = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == ELLIPSIS_CHAR:
            j = i
            while j < n and s[j] == ELLIPSIS_CHAR:
                j += 1
            run = j - i
            punct_count += (run + 1) // 2
            tokens.append((True, i, j))
            i = j
        elif ch in PUNCT_OR_SPACE:
            punct_count += 1
            tokens.append((True, i, i + 1))
            i += 1
        else:
            tokens.append((False, i, i + 1))
            i += 1
    # 把 P 计数附在最后一个 token 上返回会破坏纯度，改为独立计算路径：
    # 这里只返回 token 流；P 由 _count_punct 重新扫描（保持两个函数都简单可测）。
    _ = punct_count
    return tokens


def _count_punct(s: str) -> int:
    """P：标点+空格出现次数。U+2026 极大游程 k 个 → ceil(k/2)。"""
    total = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == ELLIPSIS_CHAR:
            j = i
            while j < n and s[j] == ELLIPSIS_CHAR:
                j += 1
            total += (j - i + 1) // 2
            i = j
        elif ch in PUNCT_OR_SPACE:
            total += 1
            i += 1
        else:
            i += 1
    return total


def check_text_rules(text: str) -> FieldTextCheck:
    """对单个字段文本执行确定性规则检查（纯函数，无 IO）。

    步骤（设计 §3.4:185-190）：
    1. 剔除换行（NEWLINE_CHARS）得 S；L = len(S)，空格计入 L；
    2. P = 标点+空格出现次数（…… 成对消耗见 _tokenize/_count_punct）；
    3. length_ok = L >= MIN_LENGTH；ratio_ok = RATIO_NUMERATOR * P <= L（整数精确）；
    4. 连续标点：token 流中相邻两个标点/空格类 token 即违规（含『， 。』——
       空格属标点类不可绕过，PRD §29.2.6）；每个极大连续游程报一条 violation；
    5. 空文本安全：L=0 → 仅 length 违规，无除法，不抛异常。

    语义类检查（无意义/敷衍/雷同）不在本函数范围（PRD §29.2.8：由模型承担，
    只出意见不直接退回；待线 A6/A7/A8）。
    """
    s = "".join(ch for ch in text if ch not in NEWLINE_CHARS)
    length = len(s)
    punct = _count_punct(s)

    violations: list[Violation] = []

    length_ok = length >= MIN_LENGTH
    if not length_ok:
        violations.append(
            Violation(
                type="length_below_minimum",
                message=(
                    f"文本长度不足：L={length} < {MIN_LENGTH}"
                    if length
                    else f"文本为空：L=0 < {MIN_LENGTH}（剔除换行后无内容）"
                ),
            )
        )

    ratio_ok = RATIO_NUMERATOR * punct <= length
    if not ratio_ok:
        violations.append(
            Violation(
                type="punctuation_ratio_exceeded",
                message=(
                    f"标点比例超限：P={punct}，L={length}，"
                    f"10×P={RATIO_NUMERATOR * punct} > L={length}（要求 P ≤ L 的 10%）"
                ),
            )
        )

    # 连续标点：token 流上找极大的“标点/空格类 token 连续游程”，每游程一条 violation。
    tokens = _tokenize(s)
    consecutive: list[Violation] = []

    def _violation(frag_start: int, frag_end: int) -> Violation:
        fragment = s[frag_start:frag_end]
        return Violation(
            type="consecutive_punctuation",
            message=(
                "连续标点（含空格分隔的标点仍判连续；…… 成对消耗后除外）："
                f"位置 {frag_start} 起 “{fragment}”"
            ),
            position=frag_start,
            fragment=fragment,
        )

    run_start: int | None = None  # 当前连续游程的起始 token 下标
    for idx, (is_punct, _start, _end) in enumerate(tokens):
        if is_punct:
            if run_start is None:
                run_start = idx
        else:
            # 游程被当前非标点 token 终止：切片止于**游程内最后一个标点 token**，
            # 不含终止字符（曾经把 end 写成当前 token 的止点，多吞一个非标点字符）。
            if run_start is not None and idx - run_start >= 2:
                consecutive.append(
                    _violation(tokens[run_start][1], tokens[idx - 1][2])
                )
            run_start = None
    # 收尾：游程延伸到串尾（最后一个 token 必属游程，直接用其止点）
    if run_start is not None and len(tokens) - run_start >= 2:
        consecutive.append(_violation(tokens[run_start][1], tokens[-1][2]))

    violations.extend(consecutive)

    return FieldTextCheck(
        length=length,
        punctuation=punct,
        length_ok=length_ok,
        ratio_ok=ratio_ok,
        no_consecutive_punct=not consecutive,
        valid=length_ok and ratio_ok and not consecutive,
        violations=tuple(violations),
    )


#: 三字段的稳定标识（对齐 Java cps_initial_review_item.field_name 的取值）。
FIELD_NAMES = ("reason", "short_term", "long_term")


def check_rectification_texts(
    *,
    reason: str,
    short_term_measure: str,
    long_term_measure: str,
) -> dict[str, FieldTextCheck]:
    """三字段分别独立计算（PRD §29.2.4/29.2.5：禁合并），互不影响。"""
    return {
        "reason": check_text_rules(reason),
        "short_term": check_text_rules(short_term_measure),
        "long_term": check_text_rules(long_term_measure),
    }
