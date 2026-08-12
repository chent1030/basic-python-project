"""Harness 框架 —— 统一 Context 对象 + CheckResult 契约。

Context 贯穿整个 Pipeline,每个 Stage 读/写它(不只是文本,还能传图片/结构化数据)。
CheckResult 是所有检查项的统一输出格式(汇总不用猜格式)。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    """检查结果的统一契约。所有检查项都返回这个。

    severity: high(必须改) / medium(建议改) / low(可选) / info(信息)
    """

    name: str
    passed: bool
    severity: str = "info"
    issues: list[str] = field(default_factory=list)
    suggestion: str = ""
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "issues": self.issues,
            "suggestion": self.suggestion,
            "detail": self.detail,
        }


@dataclass
class HarnessContext:
    """贯穿整个 Pipeline 的上下文,每个 Stage 读/写它。

    生命周期:Pipeline 创建 → preprocess 填充 file_type/raw_file/processed_images
    → ocr 填充 ocr_text/ocr_images → extract 填充 sections
    → checks 填充 check_results → report 填充 report。
    """

    # ---- 输入(外部传入)----
    entity: dict[str, Any]           # 业务数据(比对基准:供应商/人员/项目等)
    file_url: str                    # 原始文件 URL

    # ---- Stage 产出(逐步填充)----
    file_type: str = ""              # image / docx / pdf(预处理判断)
    raw_file: bytes = b""            # 下载的原始文件字节
    processed_images: list[bytes] = field(default_factory=list)  # 预处理后图片(分割+放大)
    ocr_text: str = ""               # OCR 结果(markdown)
    ocr_images: list[str] = field(default_factory=list)  # OCR 返回的图片(URL/base64)
    sections: dict[str, str] = field(default_factory=dict)  # 提取的章节
    check_results: dict[str, CheckResult] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)

    # ---- 元信息 ----
    pipeline_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pipeline_id:
            self.pipeline_id = uuid.uuid4().hex


__all__ = ["HarnessContext", "CheckResult"]
