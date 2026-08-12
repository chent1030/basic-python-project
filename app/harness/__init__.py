"""Harness 框架 —— 通用流水线编排:预处理 → OCR → 章节提取 → 并行检查 → 报告。

核心抽象:Pipeline(编排) → Stage(阶段) → Check(检查项,一文件一个,自动发现)。
Stage 间用 HarnessContext 传递(文本/图片/结构化数据)。
复用 app/ai 的 llm/工具/http_client/config/logging。

加检查项 = 在 checks/ 加一个 .py 文件(含 CHECK = XxxCheck()),自动发现。
"""
from __future__ import annotations

from app.harness.base import (
    BaseCheck,
    BaseStage,
    extract_sections,
    pick_sections,
)
from app.harness.context import CheckResult, HarnessContext
from app.harness.pipeline import Pipeline

__all__ = [
    "Pipeline",
    "BaseStage",
    "BaseCheck",
    "HarnessContext",
    "CheckResult",
    "extract_sections",
    "pick_sections",
]
