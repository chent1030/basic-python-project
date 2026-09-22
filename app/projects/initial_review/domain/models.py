"""初审域 API 模型（契约 C-01/C-03 的请求/响应体）。

对齐《后端架构与技术设计考虑》§1.2 契约定义与 Java 侧
cps_initial_review_task / cps_initial_review_item 的字段命名。
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AttachmentRef(BaseModel):
    """整改前后图片引用：object_key（对象存储，正式形态）与 content_base64（过渡形态）二选一。

    过渡期 Java 可能直传 base64；两种形态都接受，但同一附件只能给一种
    （契约 §1.2 C-01 Body 定义）。语义检查（视觉/雷同）本波次为
    NOT_IMPLEMENTED，附件仅留元数据指纹用于幂等比对，不落完整 base64。
    """

    model_config = ConfigDict(extra="forbid")

    attachment_id: str | None = Field(default=None, max_length=64)
    object_key: str | None = Field(default=None, max_length=512)
    content_base64: str | None = Field(default=None)
    file_name: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> AttachmentRef:
        provided = [x for x in (self.object_key, self.content_base64) if x]
        if len(provided) != 1:
            raise ValueError(
                "attachment 必须且只能提供 object_key 或 content_base64 其中之一"
            )
        return self

    def fingerprint_meta(self) -> dict[str, Any]:
        """存入 input_snapshot 的元数据（剥离 base64 原文，只留摘要，防 PG 膨胀）。"""
        meta: dict[str, Any] = {"attachment_id": self.attachment_id, "file_name": self.file_name}
        if self.object_key:
            meta["object_key"] = self.object_key
        if self.content_base64:
            digest = hashlib.sha256(self.content_base64.encode("ascii", "ignore")).hexdigest()
            meta["content_base64_sha256"] = digest
            meta["content_base64_length"] = len(self.content_base64)
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
