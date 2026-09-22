"""A7 视觉比对（IMAGE_COMPARE）：qwen-vl 多图输入的领域类型。

- ImageCompareResult 是模型结构化输出 schema（function calling）；
- 判定语义（prompt 版本 image-compare/qwen-vl@1 见 infrastructure/prompts.py）：
  PASS=后照片可见与问题对应的整改痕迹且前后对应同一部位；
  WARN=可见变化但关联弱/质量不足需复核；FAIL=无明显整改痕迹或部位不对应。
- 读数核对（任务书「读数相似性」）并入同一次视觉调用：仅当 issue_snapshot 携带
  提交读数时要求模型比对，否则 readings.provided=False（不单独成项，Java 契约五类
  check_type 不变；口径待确认已标注设计文档）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ImageCompareResult(BaseModel):
    """前后照片比对判定（模型结构化输出契约）。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "FAIL", "WARN"]
    reason: str = Field(description="判定理由（中文，一句话）")
    confidence: float = Field(ge=0.0, le=1.0, description="判定置信度 0-1")
    evidence_before: list[str] = Field(
        default_factory=list, description="整改前照片中的关键依据（每条一句话）"
    )
    evidence_after: list[str] = Field(
        default_factory=list, description="整改后照片中的关键依据（每条一句话）"
    )
    readings_observed: str | None = Field(
        default=None, description="照片中读到的仪表/指示值（仅当要求读数核对）"
    )
    readings_match: bool | None = Field(
        default=None, description="读数与提交值是否一致（仅当要求读数核对）"
    )


__all__ = ["ImageCompareResult"]
