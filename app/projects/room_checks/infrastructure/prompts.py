"""B6 视觉点检 prompt 常量（版本化，对齐 initial_review/prompts.py 约定）。

约定：prompt 为代码常量 + 显式版本号；格式 {用途}/{模型}@{主版本}，
变更内容必须递增主版本并同步设计文档。
"""

from __future__ import annotations

ROOM_TYPE_MATCH_PROMPT_VERSION = "room-type-match/qwen-vl@1"
ROOM_CONTENT_JUDGE_PROMPT_VERSION = "room-content-judge/qwen-vl@1"

#: 多照片时逐张标注上限（防 prompt 膨胀；数量上限另由配置约束）
_PHOTO_LABEL_MAX = 8


def _room_line(room_name: str | None) -> str:
    return f"\n【辅房】{room_name.strip()}" if room_name and room_name.strip() else ""


def build_type_match_prompt(
    *, item_type: str, item_content: str, room_name: str | None, photo_count: int
) -> str:
    """阶段①：类型匹配校验 prompt（版本 room-type-match/qwen-vl@1）。

    PRD 24.1/AC-08：照片主体必须是点检项要求的类型（如"地面"）；
    不符 → 必须重拍，不进入合格判断。
    """
    n = min(photo_count, _PHOTO_LABEL_MAX)
    return (
        "你是辅房点检照片审核助手。点检项要求拍摄的对象类型是「"
        f"{item_type.strip()}」，检查内容是「{item_content.strip()}」。"
        f"{_room_line(room_name)}\n"
        f"下面提供 {photo_count} 张照片（按给出顺序编号 1..{n}）。\n"
        "【任务】逐张识别照片主体，判断是否所有照片的主体均符合要求的对象类型。\n"
        "【判定标准】\n"
        "- type_match=true：每张照片的主体都是（或清晰包含）要求的类型对象；\n"
        "- type_match=false：任一照片主体不是要求的类型（如拍成桌面、天花板、无关物品）；"
        "reason 须指出第几张不符及其实际拍到的内容。\n"
        "只输出一个 JSON 对象："
        '{"type_match": true|false, "photo_subject": "照片实际主体简述", '
        '"reason": "判定理由"}'
    )


def build_content_judge_prompt(
    *, item_type: str, item_content: str, room_name: str | None, photo_count: int
) -> str:
    """阶段②：内容判定 prompt（版本 room-content-judge/qwen-vl@1）。

    前置：类型已匹配。PRD 24.1：按点检内容判断合格/不合格并给理由；
    AC-09：模糊/遮挡无法判定 → UNJUDGEABLE（须补拍），不得猜。
    """
    return (
        "你是辅房点检照片审核助手。照片主体已确认是「"
        f"{item_type.strip()}」，请只按点检内容判断状态。\n"
        f"【点检内容】{item_content.strip()}"
        f"{_room_line(room_name)}\n"
        f"下面提供 {photo_count} 张照片。\n"
        "【判定标准】\n"
        '- verdict="PASS"：照片内容证明满足点检内容（合格）；\n'
        '- verdict="FAIL"：照片内容明确显示不满足（不合格），reason 须具体指出'
        "问题（如哪里有杂物/积水/破损）；\n"
        '- verdict="UNJUDGEABLE"：照片模糊、遮挡、光线不足或视角不全，无法可靠判定'
        "（须补拍），不得猜测；\n"
        "【evidence】写出照片中支持判定的可观察事实（位置、物品、状态）。\n"
        "只输出一个 JSON 对象："
        '{"verdict": "PASS"|"FAIL"|"UNJUDGEABLE", "reason": "判定理由", '
        '"evidence": "照片可观察事实", "confidence": 0.0-1.0}'
    )


__all__ = [
    "ROOM_CONTENT_JUDGE_PROMPT_VERSION",
    "ROOM_TYPE_MATCH_PROMPT_VERSION",
    "build_content_judge_prompt",
    "build_type_match_prompt",
]
