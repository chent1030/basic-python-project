"""A8 文本有效性（TEXT_VALIDITY）：qwen-plus 三字段语义检查的领域类型。

- FieldValidity 既是模型结构化输出 schema（function calling），也是落库字段结果类型；
- 判定语义（prompt 版本 text-validity/qwen-plus@1 见 infrastructure/prompts.py）：
  PASS=具体且与问题相关；WARN=相关但笼统需人工复核；FAIL=空洞敷衍/答非所问/不可理解。

降级语义（详见设计文档 §2.2）：
- 调用超时/异常/输出两次无效 → status=DEGRADED、verdict=SKIPPED（不伪造内容结论）；
- 模型不可用（无 API key/未装配/显式禁用） → status=SKIPPED、verdict=SKIPPED+原因。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VALIDITY_VERDICTS = ("PASS", "FAIL", "WARN")


class FieldValidity(BaseModel):
    """单字段语义有效性判定（模型结构化输出契约）。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "FAIL", "WARN"]
    reason: str = Field(description="判定理由（中文，一句话）")
    confidence: float = Field(ge=0.0, le=1.0, description="判定置信度 0-1")
    problem_fragment: str | None = Field(
        default=None, description="最能体现问题的原文片段（FAIL/WARN 时给，≤50字）"
    )


#: 三领域字段 → 展示名（prompt 与落库记录用）
FIELD_LABELS = {
    "reason": "整改原因",
    "short_term_measure": "短期措施",
    "long_term_measure": "长期措施",
}

FIELD_ORDER = ("reason", "short_term_measure", "long_term_measure")


def degraded_field_result(field_name: str, error: str) -> dict:
    """执行失败（超时/异常/输出无效）→ 不伪造内容结论。"""
    return {
        "status": "DEGRADED",
        "verdict": "SKIPPED",
        "reason": f"语义检查执行失败，未出具结论（不伪造）：{error}",
        "confidence": None,
        "problem_fragment": None,
        "error": error,
        "field": field_name,
    }


def skipped_field_result(field_name: str, reason: str) -> dict:
    """模型不可用/未装配 → SKIPPED+原因。"""
    return {
        "status": "SKIPPED",
        "verdict": "SKIPPED",
        "reason": reason,
        "confidence": None,
        "problem_fragment": None,
        "error": None,
        "field": field_name,
    }


def model_field_result(field_name: str, result: FieldValidity) -> dict:
    """模型正常返回 → IMPLEMENTED 记录。"""
    return {
        "status": "IMPLEMENTED",
        "verdict": result.verdict,
        "reason": result.reason,
        "confidence": round(result.confidence, 4),
        "problem_fragment": result.problem_fragment,
        "error": None,
        "field": field_name,
    }


__all__ = [
    "FIELD_LABELS",
    "FIELD_ORDER",
    "FieldValidity",
    "VALIDITY_VERDICTS",
    "degraded_field_result",
    "model_field_result",
    "skipped_field_result",
]
