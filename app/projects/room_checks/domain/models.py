"""B6 判定链领域模型（契约 C-04 / PRD §23-24）。

状态口径（C-04 两阶段返回，同步 API）：
- ``TYPE_MISMATCH``：照片与点检项类型不符 → 须重拍，不进入合格判断（AC-08）；
- ``JUDGED``：内容判定完成，verdict=PASS|FAIL + 理由（PRD 24.1 内容判断）；
- ``UNJUDGEABLE``：模糊/遮挡/光线不足等无法判定 → 须补拍并阻止提交（AC-09）；
- ``SKIPPED``：模型检查禁用/配置缺失等前置条件不具备 → 不伪造判定（A7 原则）。

技术失败（网络/超时/输出两次无效）不落上述业务状态：服务层抛
JudgeTechnicalError，端点转 HTTP 502（不缓存，Java 可用同幂等键重试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

# --- C-04 响应状态 ---------------------------------------------------------
STATUS_TYPE_MISMATCH = "TYPE_MISMATCH"
STATUS_JUDGED = "JUDGED"
STATUS_UNJUDGEABLE = "UNJUDGEABLE"
STATUS_SKIPPED = "SKIPPED"
ALL_STATUSES = (
    STATUS_TYPE_MISMATCH,
    STATUS_JUDGED,
    STATUS_UNJUDGEABLE,
    STATUS_SKIPPED,
)

# --- 内容判定结论（仅 JUDGED 时有意义） -------------------------------------
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"

# --- stage_trace 子状态 -----------------------------------------------------
STAGE_TYPE_MATCH = "type_match"
STAGE_CONTENT_JUDGE = "content_judge"

#: 端点层照片数量硬上限（防滥用；模型预算上限由配置另约束）
ENDPOINT_MAX_PHOTOS = 8


# --- 视觉模型结构化输出 schema（DashScopeModelClient.complete_vision_json） ---
class TypeMatchSchema(BaseModel):
    """第一阶段：类型匹配校验输出。"""

    type_match: bool = Field(description="照片主体是否符合点检项要求类型")
    photo_subject: str = Field(default="", description="照片实际拍到的主体")
    reason: str = Field(default="", description="判定理由（含不符时指出应拍什么）")


class ContentJudgeSchema(BaseModel):
    """第二阶段：内容判定输出。"""

    verdict: Literal["PASS", "FAIL", "UNJUDGEABLE"] = Field(
        description="合格/不合格/无法判定（照片模糊遮挡等）"
    )
    reason: str = Field(default="", description="判定理由（不合格须具体指出问题）")
    evidence: str = Field(
        default="", description="照片中支持判定的可观察事实（证据留存）"
    )
    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="判定置信度 0-1"
    )


# --- 请求/结果 ---------------------------------------------------------------
@dataclass(frozen=True)
class JudgeRequest:
    """C-04 入参（点检项定义 + 照片 object keys）。

    幂等键契约：``room-judge-{submissionId}-{itemId}-{attempt}``（Java 侧同源
    生成；缺省时服务端按此格式派生）。
    """

    idempotency_key: str
    submission_id: str
    item_id: str
    attempt: int  # ≥1；同一 item 重拍/补拍后递增
    item_content: str  # 点检内容（如"地面无杂物、无积水"）
    item_type: str  # 点检类型=照片应含对象类别（如"地面"；≠辅房类型）
    photo_object_keys: tuple[str, ...]  # RustFS object keys（1..max）
    deduction: float | None = None  # 扣分值（透传留痕，判定不用）
    config_version: str | None = None  # 点检项配置版本（留痕）
    room_name: str | None = None  # 辅房名称（prompt 上下文，可选）


@dataclass(frozen=True)
class JudgeResult:
    """C-04 响应（证据留存：stage_trace + 照片引用 + 版本锚点）。"""

    idempotency_key: str
    submission_id: str
    item_id: str
    attempt: int
    status: str  # ALL_STATUSES 之一
    verdict: str | None  # PASS|FAIL；仅 status=JUDGED 时非空
    reason: str
    evidence: str
    photo_object_keys: tuple[str, ...]
    item_snapshot: dict  # {content,type,deduction,config_version} 执行留痕（PRD 23.2）
    stage_trace: dict = field(default_factory=dict)
    model_version: str = ""
    prompt_versions: dict = field(default_factory=dict)
    judged_at: str = ""
    replayed: bool = False


__all__ = [
    "ALL_STATUSES",
    "ContentJudgeSchema",
    "ENDPOINT_MAX_PHOTOS",
    "JudgeRequest",
    "JudgeResult",
    "STAGE_CONTENT_JUDGE",
    "STAGE_TYPE_MATCH",
    "STATUS_JUDGED",
    "STATUS_SKIPPED",
    "STATUS_TYPE_MISMATCH",
    "STATUS_UNJUDGEABLE",
    "TypeMatchSchema",
    "VERDICT_FAIL",
    "VERDICT_PASS",
]
