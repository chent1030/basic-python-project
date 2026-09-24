"""B6 三级判定 prompt 模板构建器（PRD §23.2 「拍图判断」）。

三级判定每一级对应一个独立 prompt 模板：
- 一级 ``type_match``：从照片元数据/视觉推断主体类型，与
  ``expected_match_type`` 对齐——结构化输出 ``{"matched": bool,
  "observed_type": str, "reason": str}``；
- 二级 ``content``：核对照片内容是否覆盖 ``expected_keywords``——结构化
  输出 ``{"content_match": bool, "matched_keywords": [...], "missing_keywords":
  [...], "notes": str}``；
- 三级 ``evidence``：评估证据具体性——结构化输出 ``{"evidence_strength":
  "STRONG|MODERATE|WEAK|NONE", "evidence_notes": str, "visual_features":
  list[str]}``。

所有 prompt 末尾强制附 ``_JSON_ONLY_RULE``（对齐波次 8 J 线遗留的
``prompts.py`` 处 v2 同模式），配合 ``extract_json_object`` 多级降级
解析保证输出稳定。

版本号格式：``room-check/{stage}/qwen-vl-plus@{major}``，变更内容必须
递增 major 并同步设计文档（与 initial_review/infrastructure/prompts.py
的 ``TEXT_VALIDITY_PROMPT_VERSION`` 等约定一致）。
"""

from __future__ import annotations

from typing import Any

from app.projects.room_checks.domain.evidence_models import (
    RoomCheckEvidence,
    RoomType,
)

# 版本锚点（写入 RoomCheckVerdict.raw_output 便于回溯）
PROMPT_VERSION_TYPE_MATCH = "room-check/type-match/qwen-vl-plus@1"
PROMPT_VERSION_CONTENT = "room-check/content/qwen-vl-plus@1"
PROMPT_VERSION_EVIDENCE = "room-check/evidence/qwen-vl-plus@1"

# 波次 8（J 线遗留 #2）：输出格式硬约束。多级降级解析（initial_review
# 的 extract_json_object）已能容忍，但仍有约 2% 失败率——v2 prompt 强制
# JSON-only 可将失败率压到 < 0.5%。
_JSON_ONLY_RULE = (
    "\n【输出格式·强制】你的回复必须是且仅是一个 JSON 对象：以 { 开头、以 } 结尾，"
    "禁止输出任何解释文字、前后缀或 markdown 代码栅栏。"
)


def _room_type_line(room_type: RoomType) -> str:
    """辅房类型上下文（中文标签便于模型语义对齐）。"""
    labels = {
        RoomType.PRIMARY: "主控室",
        RoomType.STANDARD: "普通辅房",
        RoomType.SPECIAL: "特殊辅房（如电缆夹层）",
        RoomType.TOOL: "工器具室",
        RoomType.OTHER: "其他",
    }
    return labels.get(room_type, room_type.value)


class PromptBuilder:
    """三级判定 prompt 模板构建器（参数化 RoomCheckEvidence 字段）。

    用法：
        builder = PromptBuilder()
        prompt = builder.build_type_match_prompt(evidence, observed_meta={"filename": "ground.jpg"})

    与直接拼接字符串相比，集中在一处便于：
    1. 单测验证 prompt 注入 ``expected_keywords`` 等字段；
    2. prompt 版本变更时只改一处；
    3. Java 端模拟 prompt 拼接时不需要重新理解格式。
    """

    def __init__(self) -> None:
        # 不持有状态（保持 stateless 便于并发复用）
        pass

    def build_type_match_prompt(
        self,
        evidence: RoomCheckEvidence,
        *,
        observed_meta: dict[str, Any] | None = None,
    ) -> str:
        """一级 type-match：照片主体类型是否匹配 expected_match_type。

        Args:
            evidence: B6 入参（room_type/expected_match_type 等）。
            observed_meta: 照片元数据（EXIF/filename/upload tag），用于
                在 prompt 中显式标注给模型参考。None 时省略元数据段落。
        """
        meta_section = ""
        if observed_meta:
            parts = [f"{k}: {v}" for k, v in observed_meta.items() if v]
            if parts:
                meta_section = "\n【照片元数据】\n" + "\n".join(parts) + "\n"

        expected = (evidence.expected_match_type or "").strip() or "（未指定）"
        return (
            "你是电网辅房点检照片审核助手。\n"
            f"【辅房类型】{_room_type_line(evidence.room_type)}\n"
            f"【期望照片主体】{expected}\n"
            f"{meta_section}"
            "【任务】判断照片主体类型是否与「期望照片主体」一致（语义等价即可，"
            "不必字面一致）。\n"
            "【判定标准】\n"
            "- matched=true：主体一致（如期望「地面」→ 照片主体是地面/地板/瓷砖地面等）；\n"
            "- matched=false：主体不一致（如期望「地面」→ 实际拍到桌面/天花板/无关物品），"
            "reason 须指出实际看到的主体。\n"
            + _JSON_ONLY_RULE
            + "\n字段与取值示例：\n"
            '{"matched": true|false, "observed_type": "照片主体简述", '
            '"reason": "判定理由"}'
        )

    def build_content_prompt(self, evidence: RoomCheckEvidence) -> str:
        """二级 content：照片内容是否覆盖 expected_keywords。

        ``expected_keywords`` 为空时跳过关键词核对，只判断「content 是否清晰可读」
        （避免无谓失败）。
        """
        kw_section = ""
        if evidence.expected_keywords:
            kw_section = (
                "\n【期望关键物证】（必须全部出现在照片中）\n"
                + "\n".join(f"- {kw.strip()}" for kw in evidence.expected_keywords if kw.strip())
                + "\n"
            )

        return (
            "你是电网辅房点检照片审核助手。\n"
            f"【辅房类型】{_room_type_line(evidence.room_type)}\n"
            f"【点检项ID】{evidence.check_item_id}\n"
            f"{kw_section}"
            "【任务】核对照片内容是否覆盖上述关键物证，并判断照片是否清晰可读。\n"
            "【判定标准】\n"
            "- content_match=true：所有期望关键物证均可见，照片清晰可读；\n"
            "- content_match=false：关键物证缺失或被遮挡；missing_keywords 列出缺失项；\n"
            "- notes：简要描述照片内容（中文一句话）。\n"
            + _JSON_ONLY_RULE
            + "\n字段与取值示例：\n"
            '{"content_match": true|false, "matched_keywords": ["..."], '
            '"missing_keywords": ["..."], "notes": "一句话描述"}'
        )

    def build_evidence_prompt(
        self,
        evidence: RoomCheckEvidence,
        *,
        content_match: bool,
        matched_keywords: tuple[str, ...],
        missing_keywords: tuple[str, ...],
    ) -> str:
        """三级 evidence：基于前两级结论综合评估证据强度。

        评估维度：
        1. ``evidence_strength``: STRONG(具体可定位)/ MODERATE(可见但笼统) /
           WEAK(模糊仅大致可见)/ NONE(完全不可见)；
        2. ``evidence_notes``: 一句话中文评估；
        3. ``visual_features``: 列出照片中支持判定的可观察事实（位置/物品/状态）。
        """
        summary = (
            f"前置判定：content_match={'true' if content_match else 'false'}；"
            f"matched={list(matched_keywords)}；"
            f"missing={list(missing_keywords)}。"
        )
        return (
            "你是电网辅房点检照片审核助手。基于前置判定结果综合评估证据强度。\n"
            f"【辅房类型】{_room_type_line(evidence.room_type)}\n"
            f"【点检项ID】{evidence.check_item_id}\n"
            f"【前置结论】{summary}\n"
            "【任务】评估「证据强度」（STRONG/MODERATE/WEAK/NONE）。\n"
            "【判定标准】\n"
            "- STRONG：照片清晰且能定位到具体物证（如「照片左上角可见锈蚀接地扁铁」）；\n"
            "- MODERATE：照片可读但描述笼统（如「地面较干净」无具体证据）；\n"
            "- WEAK：照片模糊或仅大致可见；\n"
            "- NONE：照片不可读或与点检项无关。\n"
            + _JSON_ONLY_RULE
            + "\n字段与取值示例：\n"
            '{"evidence_strength": "STRONG|MODERATE|WEAK|NONE", '
            '"evidence_notes": "一句话中文评估", '
            '"visual_features": ["具体观察1", "具体观察2"]}'
        )


__all__ = [
    "PROMPT_VERSION_CONTENT",
    "PROMPT_VERSION_EVIDENCE",
    "PROMPT_VERSION_TYPE_MATCH",
    "PromptBuilder",
]