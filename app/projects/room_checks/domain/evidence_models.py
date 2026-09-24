"""B6 视觉点检 Agent 三级判定领域模型（PRD §23 / §30.1 辅房点检拍图判断）。

与 ``app/projects/room_checks/domain/models.py`` 中的两阶段契约
（``JudgeRequest``/``JudgeResult``，状态机=TYPE_MISMATCH/JUDGED/UNJUDGEABLE/
SKIPPED）并存：两阶段服务于 Java 端同步提交即返回的旧接口（C-04）；
本模块面向 B6「拍图判断」三级判定算法（type-match → content → evidence），
输出 ``overall = PASS|PARTIAL|PROBLEM`` + 0-100 整数 score。

设计选择（三级 vs 一次模型调用）：
- **抗干扰**：单次调用要求模型同时关注「照片类型」「是否漏检关键物证」
  「结论措辞」三件事，模型方差大；三级拆分后每一级责任清晰，便于
  单级回退/重试/审计；
- **审计可回放**：每一级可独立留 raw_output，便于合规抽查「模型在某
  个点上答错了哪个具体事实」；
- **降级明确**：任何一级失败/超时 → 固定 fallback（PROBLEM/0 + reason
  「视觉判定超时/失败，建议人工复检」），与 Java 契约约定一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RoomType(str, Enum):  # noqa: UP042
    """辅房类别（与 Java 端 ``cps_room.storage_room_type`` 枚举对齐）。

    Java 端 DDL 定义：PRIMARY(主控室)/STANDARD(普通辅房)/SPECIAL(特殊辅房，
    如电缆夹层)/TOOL(工器具室)/OTHER(其他)。Python 侧仅做字符串契约，
    不参与业务规则——校验在 Java 端进行；Python 侧只用其作为 prompt
    上下文与扣分权重参考。
    """

    PRIMARY = "PRIMARY"
    STANDARD = "STANDARD"
    SPECIAL = "SPECIAL"
    TOOL = "TOOL"
    OTHER = "OTHER"


class JudgeVerdict(str, Enum):  # noqa: UP042
    """三级判定最终结论（与 PRD §23.2 「拍图判断」字段语义一致）。"""

    PASS = "PASS"  # score >= 80：照片主体一致 + 关键物证匹配 + 证据具体
    PARTIAL = "PARTIAL"  # 50 <= score < 80：局部缺失或证据含糊，需人工复核
    PROBLEM = "PROBLEM"  # score < 50：类型不符 / 内容不符 / 解析失败 / 超时


@dataclass(frozen=True)
class RoomCheckEvidence:
    """B6 入参：点检项定义 + 照片引用 + 期望匹配项。

    字段命名对齐 Java 端 ``cps_room_check_evidence`` DTO，便于
    ``JavaRoomCheckCallbackClient`` 回传时字段直接映射。

    Attributes:
        fingerprint: 幂等键（同一指纹 30 分钟内复用 verdict 缓存；Java
            端通常用 ``room-check-{checkItemId}-{submissionId}-{attempt}``）。
        room_type: 辅房类型（与 ``RoomType`` 一致）。
        check_item_id: 点检项 ID（与 Java 端 ``cps_room_check_item.id`` 对齐）。
        photo_object_key: RustFS 对象键（object_key，逗号分隔多张）。
        photo_url: 可选 HTTP URL（绕开 object_key 直接送视觉模型；与
            object_key 互斥——优先使用 object_key 走 RustFS 凭据链）。
        expected_match_type: 期望照片主体类型（如「地面」「配电柜」），用于
            一级 type-match 校验。
        expected_keywords: 期望出现的关键物证（逗号或列表分隔），用于二级
            content 关键词覆盖率评分。
        submitted_at: 提交时间（ISO8601；端点层注入，避免调用方时区漂移）。
    """

    fingerprint: str
    room_type: RoomType
    check_item_id: str
    photo_object_key: str
    photo_url: str | None = None
    expected_match_type: str | None = None
    expected_keywords: tuple[str, ...] = ()
    submitted_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RoomCheckVerdict:
    """B6 出参：三级判定最终结论。

    Attributes:
        fingerprint: 同入参，贯穿以便 callback 一一对应。
        overall: PASS|PARTIAL|PROBLEM。
        score: 0-100 整数（100 起始，按扣分规则递减；负数或越界由服务层
            收尾，构造器不强制——便于序列化时观测真实值）。
        reasons: 人类可读扣分/判定理由列表（按顺序拼接展示给 Java）。
        judged_at: 判定时间（ISO8601）。
        model_name: 实际调用的视觉模型名（如 ``qwen-vl-plus``）；降级到
            fallback 时为空字符串。
        raw_output: 原始模型输出（调试/合规留痕；三级 raw 拼成 dict
            ``{"type_match": ..., "content": ..., "evidence": ...}``）。
    """

    fingerprint: str
    overall: JudgeVerdict
    score: int
    reasons: tuple[str, ...]
    judged_at: datetime
    model_name: str
    raw_output: dict[str, str] = field(default_factory=dict)


__all__ = [
    "JudgeVerdict",
    "RoomCheckEvidence",
    "RoomCheckVerdict",
    "RoomType",
]