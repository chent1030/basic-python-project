"""初审域 API 模型（契约 C-01/C-03 的请求/响应体）。

对齐《后端架构与技术设计考虑》§1.2 契约定义与 Java 侧
cps_initial_review_task / cps_initial_review_item 的字段命名。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AttachmentRef(BaseModel):
    """整改前后图片引用：只有 ``url`` 一种形态（MinIO 公开绝对 URL）。

    由 Python 侧对 URL 发起 HTTP(S) GET 后转 data URL 送视觉模型；没有 URL
    或取不到图时按「不判定」（SKIPPED/DEGRADED）处理，不做参数校验。

    语义检查（视觉/雷同）本波次为 NOT_IMPLEMENTED，附件仅留元数据指纹用于
    幂等/重放比对；``url`` 只落 URL 本身，重放时按 URL 重新拉取。
    """

    model_config = ConfigDict(extra="forbid")

    attachment_id: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2048)
    file_name: str | None = Field(default=None, max_length=256)

    def fingerprint_meta(self) -> dict[str, Any]:
        """存入 input_snapshot 的元数据（保留 URL 以便重放时重新拉取）。"""
        meta: dict[str, Any] = {
            "attachment_id": self.attachment_id,
            "file_name": self.file_name,
        }
        if self.url:
            meta["url"] = self.url
        return meta


class RectificationReviewRequest(BaseModel):
    """C-01：Java → Python 创建初审任务。

    幂等键（task_ref）由服务端按公式 ``cps-rectify-{issue_id}-v{version_no}`` 派生，
    客户端不传（设计 §3.2 幂等键表）。
    """

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1, max_length=64)
    submission_id: str = Field(min_length=1, max_length=64)
    version_no: int = Field(ge=1)
    reason: str = Field(default="", max_length=4000)
    short_term_measure: str = Field(default="", max_length=4000)
    long_term_measure: str = Field(default="", max_length=4000)
    before_attachments: list[AttachmentRef] = Field(default_factory=list, max_length=20)
    after_attachments: list[AttachmentRef] = Field(default_factory=list, max_length=20)
    #: 问题单快照（标题/严重度等上下文，供后续语义检查使用；本波次仅透传留存）
    issue_snapshot: dict[str, Any] | None = None
