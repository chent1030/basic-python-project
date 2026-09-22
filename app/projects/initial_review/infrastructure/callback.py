"""C-02 回调客户端：Python → Java POST /api/callbacks/initial-review/result。

契约（设计 §1.2 C-02 / §3.1 管线 / §3.2 幂等键表）：
- Body：idempotency_key = ``initial-review-result-{task_id}``（Java 回调端去重）；
  submission_id、version_no、逐项意见（items[]，含 L/P 实际值）、模型执行状态；
- 技术重试 ≤2 次（30s/60s 退避，不重置任务计时——这里是结果投递重试，与任务 deadline 解耦）；
- is_late 恒发 false：是否“迟到”（任务已被 Java 接管后才到达）由 Java 端按其任务状态判定置位，
  Python 不越权判断（契约：任务被接管时 is_late=true 仅留痕）。

本波次不要求调通 Java（列入待联调清单）；客户端按契约字段实现，可用假 HTTP 层测试。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from app.projects.initial_review.infrastructure.config import InitialReviewSettings

logger = logging.getLogger(__name__)


class JavaCallbackClient:
    """每请求新建 AsyncClient（波次 1 简化实现；复用连接池待联调期评估）。"""

    def __init__(
        self,
        settings: InitialReviewSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._transport = transport  # 测试注入 httpx.MockTransport

    @property
    def endpoint(self) -> str:
        return self._settings.java_callback_base + self._settings.java_callback_path

    async def send(self, payload: dict[str, Any]) -> tuple[bool, int, str | None]:
        """投递回调；返回 (是否成功, 实际尝试次数, 最后错误)。

        内部按配置做 1+max_retries 次退避重试，成功即提前返回（attempts 如实上报）。
        """
        url = self.endpoint
        max_attempts = 1 + max(self._settings.callback_max_retries, 0)
        backoff = self._settings.callback_backoff_seconds
        last_error: str | None = None
        attempts = 0
        async with httpx.AsyncClient(
            timeout=self._settings.callback_timeout_seconds, transport=self._transport
        ) as client:
            for attempt in range(1, max_attempts + 1):
                attempts = attempt
                try:
                    resp = await client.post(url, json=payload)
                    if 200 <= resp.status_code < 300:
                        return True, attempts, None
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                if attempt < max_attempts:
                    delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0.0
                    if delay > 0:
                        await asyncio.sleep(delay)
        return False, attempts, last_error


def build_callback_payload(row, text_checks: dict[str, Any] | None) -> dict[str, Any]:
    """把 exec 行组装成 C-02 回调 Body（字段名对齐 Java cps_initial_review_item）。

    items 覆盖五检查项（PRD §29.1）：
    - TEXT_LENGTH / PUNCTUATION_RATIO：三字段逐项（verdict PASS/FAIL + L/P 实际值 + ratio_ok）；
    - TEXT_VALIDITY（语义有效性）：verdict=SKIPPED +
      implementation_status=NOT_IMPLEMENTED（待线 A7）；
    - IMAGE_COMPARE（前后图视觉）/ MEASURE_SIMILARITY（短长期措施雷同）：
      verdict=SKIPPED + implementation_status=NOT_IMPLEMENTED（待线 A6/A8）。
    「部分检查缺失记 SKIPPED，不得伪造通过」（设计 §2.2(e) / PRD §28.4）。
    """
    checks = text_checks or {}
    items: list[dict[str, Any]] = []
    for field in ("reason", "short_term", "long_term"):
        fc = checks.get(field) or {}
        items.append(
            {
                "check_type": "TEXT_LENGTH",
                "field_name": field,
                "verdict": "PASS" if fc.get("length_ok") else "FAIL",
                "text_length": fc.get("length"),
                "reason": None if fc.get("length_ok") else "文本长度不足 15 字（剔除换行后）",
            }
        )
        items.append(
            {
                "check_type": "PUNCTUATION_RATIO",
                "field_name": field,
                "verdict": "PASS" if fc.get("ratio_ok") else "FAIL",
                "text_length": fc.get("length"),
                "punctuation_count": fc.get("punctuation"),
                "ratio_ok": fc.get("ratio_ok"),
                "reason": (
                    None
                    if fc.get("ratio_ok")
                    else f"标点比例超限：P={fc.get('punctuation')}，L={fc.get('length')}"
                ),
            }
        )
        # 连续标点违规并入 PUNCTUATION_RATIO 的意见明细
        if fc.get("violations"):
            consec = [v for v in fc["violations"] if v.get("type") == "consecutive_punctuation"]
            for v in consec:
                items.append(
                    {
                        "check_type": "PUNCTUATION_RATIO",
                        "field_name": field,
                        "verdict": "FAIL",
                        "text_length": fc.get("length"),
                        "punctuation_count": fc.get("punctuation"),
                        "ratio_ok": fc.get("ratio_ok"),
                        "reason": "存在连续标点",
                        "problem_fragment": v.get("fragment"),
                    }
                )
        items.append(
            {
                "check_type": "TEXT_VALIDITY",
                "field_name": field,
                "verdict": "SKIPPED",
                "implementation_status": "NOT_IMPLEMENTED",
                "reason": "语义有效性检查待线 A7（模型侧）接入后启用",
            }
        )
    items.append(
        {
            "check_type": "IMAGE_COMPARE",
            "field_name": None,
            "verdict": "SKIPPED",
            "implementation_status": "NOT_IMPLEMENTED",
            "reason": "整改前后图片视觉对比待线 A6 接入后启用",
        }
    )
    items.append(
        {
            "check_type": "MEASURE_SIMILARITY",
            "field_name": None,
            "verdict": "SKIPPED",
            "implementation_status": "NOT_IMPLEMENTED",
            "reason": "短长期措施雷同检测待线 A8 接入后启用",
        }
    )

    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "idempotency_key": f"initial-review-result-{row.task_id}",
        "task_id": row.task_id,
        "submission_id": row.submission_id,
        "issue_id": row.issue_id,
        "version_no": row.version_no,
        "status": row.status,
        "overall": row.overall,
        "is_late": False,  # Java 端按其任务状态判定置位
        "error": row.error,
        "error_code": row.error_code,
        "model_version": row.model_version,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "deadline_at": _iso(row.deadline_at),
        "items": items,
    }
