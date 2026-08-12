"""文档审核项目 —— 继承 harness 基类实现文档智能审核。

入口:POST /api/v1/doc-review(EhsConstruct) → 审核每个文件 → 返回报告。
核心 agent:DocReviewFlow(pipeline 拓扑)。
"""
from __future__ import annotations

from app.projects.doc_review.agents import DocReviewFlow

__all__ = ["DocReviewFlow"]
