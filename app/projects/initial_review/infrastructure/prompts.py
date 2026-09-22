"""波次 2 模型 prompt 常量（版本号即 model_version 登记锚点）。

约定：prompt 一律为代码常量 + 显式版本号（任务书：写入 config/ 或代码常量并注明版本）。
版本号格式 {用途}/{模型}@{主版本}，变更 prompt 内容必须递增主版本并同步设计文档。
"""

from __future__ import annotations

TEXT_VALIDITY_PROMPT_VERSION = "text-validity/qwen-plus@1"
IMAGE_COMPARE_PROMPT_VERSION = "image-compare/qwen-vl@1"

#: 展示名（与 domain/text_validity.FIELD_LABELS 同步）
_FIELD_LABELS = {
    "reason": "整改原因",
    "short_term_measure": "短期措施",
    "long_term_measure": "长期措施",
}

#: issue_snapshot 中可提取的问题背景键（容错提取，缺键即略）
ISSUE_CONTEXT_KEYS = (
    "title",
    "problem_description",
    "description",
    "issue_type",
    "device_name",
    "equipment_name",
    "location",
)

#: 问题背景拼接上限（字符）——防快照异常膨胀撑爆 prompt
ISSUE_CONTEXT_MAX_CHARS = 600


def build_issue_context(issue_snapshot: dict | None) -> str:
    """从 issue_snapshot 容错提取问题背景（供语义/视觉 prompt 使用）。"""
    if not isinstance(issue_snapshot, dict):
        return "（未提供）"
    parts: list[str] = []
    for key in ISSUE_CONTEXT_KEYS:
        value = issue_snapshot.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}：{value.strip()}")
    if not parts:
        return "（未提供）"
    return "；".join(parts)[:ISSUE_CONTEXT_MAX_CHARS]


def build_text_validity_prompt(
    *, field_name: str, text: str, issue_context: str
) -> str:
    """A8 单字段语义有效性 prompt（版本 text-validity/qwen-plus@1）。"""
    label = _FIELD_LABELS.get(field_name, field_name)
    return (
        "你是电网设备问题整改初审助手。请判断下面这条整改提交文本是否「语义有效」。\n"
        f"【问题背景】（来自问题单快照）{issue_context}\n"
        f"【待检字段】{label}\n"
        f"【字段内容】{text}\n"
        "【判定标准】\n"
        "- PASS：内容具体、与问题背景相关、说明了实际处置动作（长期措施应说明防复发安排），"
        "不是空洞套话；\n"
        "- WARN：内容与问题基本相关但过于笼统、信息量偏少，需要人工复核；\n"
        "- FAIL：与问题背景无关、空洞敷衍（如仅「已整改」「无异议」「加强管理」类无信息量表述）、"
        "答非所问或语义无法理解。\n"
        "【输出要求】给出 verdict/reason/confidence；FAIL 或 WARN 时尽量给出 problem_fragment"
        "（原文中最能体现问题的连续片段，不超过 50 字）。reason 用一句中文说明依据。"
    )


_READING_INSTRUCTION = (
    "\n【读数核对】提交方登记的仪表/指示读数如下，请从照片中读出对应值并判断是否一致，"
    "填入 readings_observed 与 readings_match：\n{readings}\n"
)


def build_image_compare_prompt(
    *,
    before_count: int,
    after_count: int,
    issue_context: str,
    submitted_readings: list[dict] | None,
) -> str:
    """A7 前后照片比对 prompt（版本 image-compare/qwen-vl@1）。

    submitted_readings 非空时并入读数核对指令（一次调用完成，不另发起）。
    """
    prompt = (
        "你是电网设备问题整改初审助手。按顺序给出「整改前」与「整改后」的照片。\n"
        f"【问题背景】（来自问题单快照）{issue_context}\n"
        f"【照片顺序】前 {before_count} 张为整改前，后 {after_count} 张为整改后。\n"
        "【判定标准】\n"
        "- PASS：整改后照片可见与问题背景对应的整改痕迹（更换/修复/清理/紧固/标识等），"
        "且前后照片对应同一处设备或部位；\n"
        "- WARN：可见变化但与问题的关联性弱，或照片质量/角度不足以判断，需人工复核；\n"
        "- FAIL：前后照片状态明显相同无整改痕迹、部位不对应（拍了别处）、"
        "或整改后照片与问题背景无关。\n"
        "【输出要求】verdict/reason/confidence；evidence_before 与 evidence_after "
        "分别给出关键依据（每条一句话，说明第几张照片里看到什么）。"
    )
    if submitted_readings:
        lines = "\n".join(
            f"- {r.get('name', '读数')}: {r.get('value', '')}" for r in submitted_readings
        )
        prompt += _READING_INSTRUCTION.format(readings=lines)
    return prompt


#: issue_snapshot 中读数登记的候选键（容错提取）
READING_KEYS = ("readings", "submitted_values", "meter_values")


def extract_submitted_readings(issue_snapshot: dict | None) -> list[dict]:
    """容错提取提交读数（[{name, value}]）；无 → 空列表（读数核对跳过）。"""
    if not isinstance(issue_snapshot, dict):
        return []
    for key in READING_KEYS:
        value = issue_snapshot.get(key)
        if isinstance(value, list) and value:
            readings = [
                {"name": str(r.get("name") or f"读数{i + 1}"), "value": r.get("value")}
                for i, r in enumerate(value)
                if isinstance(r, dict) and r.get("value") is not None
            ]
            if readings:
                return readings
    return []


__all__ = [
    "IMAGE_COMPARE_PROMPT_VERSION",
    "TEXT_VALIDITY_PROMPT_VERSION",
    "build_image_compare_prompt",
    "build_issue_context",
    "build_text_validity_prompt",
    "extract_submitted_readings",
]
