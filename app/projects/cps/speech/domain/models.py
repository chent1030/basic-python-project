"""F1 语音转写领域模型（契约 C-06 speech-to-text / PRD §20.2 / 决策 D-03）。

语义口径：
- 转写结果**仅用于回填表单字段**（录音 → 转写 → 用户编辑确认 → 提交），
  不作为辅房点检或任何判定证据（PRD §20.2、AC-03）——端点语义即表单辅助；
- 目标字段白名单 = D-03 拍板的三字段：原因 / 短期措施 / 长期措施，
  非白名单一律拒绝（422），不做通用转写入口；
- ``TRANSCRIBED``：ASR 转写完成，text 供 Java 回填表单；
- ``SKIPPED``：ASR 未配置/禁用/RustFS 缺失等前置条件不具备 → 不伪造转写
  （对齐 A7/B6「不伪造」原则），Java 引导用户手工输入。

技术失败（ASR 网关异常/超时/响应无效）不落上述业务状态：服务层抛
SpeechTechnicalError，端点转 HTTP 502（不缓存，Java 可用同幂等键重试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- D-03 目标字段白名单（三字段） ------------------------------------------
FIELD_REASON = "reason"  # 原因
FIELD_SHORT_TERM = "short_term"  # 短期措施
FIELD_LONG_TERM = "long_term"  # 长期措施
TARGET_FIELDS = (FIELD_REASON, FIELD_SHORT_TERM, FIELD_LONG_TERM)
FIELD_LABELS = {
    FIELD_REASON: "原因",
    FIELD_SHORT_TERM: "短期措施",
    FIELD_LONG_TERM: "长期措施",
}

# --- C-06 响应状态 -----------------------------------------------------------
STATUS_TRANSCRIBED = "TRANSCRIBED"
STATUS_SKIPPED = "SKIPPED"
ALL_STATUSES = (STATUS_TRANSCRIBED, STATUS_SKIPPED)

#: 转写链版本锚点（ASR 客户端/协议变更时递增）
SPEECH_MODEL_VERSION = "speech/asr-openai-compat@1"

#: 音频格式 → MIME（input_audio data URI 组装用；移动端录音常见格式）
AUDIO_MIME_BY_FORMAT: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "amr": "audio/amr",
    "speex": "audio/speex",
}
DEFAULT_AUDIO_FORMAT = "wav"


def audio_mime(audio_format: str) -> str:
    """格式名 → MIME；未知格式按二进制流兜底（交由 ASR 网关判别）。"""
    fmt = (audio_format or "").strip().lower().lstrip(".")
    return AUDIO_MIME_BY_FORMAT.get(fmt, "application/octet-stream")


# --- 请求/结果 ---------------------------------------------------------------
@dataclass(frozen=True)
class SpeechTranscribeRequest:
    """C-06 入参（音频引用 + 目标字段）。

    音频来源二选一（互斥，契约口径）：
    - ``audio_object_key``：RustFS 对象键（移动端先上传，Python 内网取流）；
    - ``audio_base64``：内嵌 base64 音频（联调过渡/小音频直传）。

    幂等键惯例：``speech-{submissionId}-{field}-{attempt}``（Java 侧同源生成；
    缺省时服务端按此格式派生；``request_id`` 作为 C-06 契约别名同样接受）。
    """

    idempotency_key: str
    submission_id: str
    field: str  # ∈ TARGET_FIELDS（D-03 白名单）
    attempt: int  # ≥1；同一字段重录后递增
    audio_object_key: str | None = None
    audio_base64: str | None = None
    audio_format: str = DEFAULT_AUDIO_FORMAT


@dataclass(frozen=True)
class SpeechTranscribeResult:
    """C-06 出参（转写文本 + 元数据；仅回填表单，不作点检证据）。"""

    idempotency_key: str
    submission_id: str
    field: str
    attempt: int
    status: str  # TRANSCRIBED | SKIPPED
    text: str = ""
    confidence: float | None = None  # provider 支持时返回；qwen3-asr-flash 不提供 → None
    duration_seconds: float | None = None  # provider usage 返回时透传
    language: str | None = None
    model: str = ""
    audio_object_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    transcribed_at: str = ""
    replayed: bool = False
