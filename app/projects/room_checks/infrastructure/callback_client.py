"""B6 视觉点检 → Java 回调客户端（契约 B6-CB-01）。

Java 端契约（PRD §23.2 拍图判断 / §30.1 辅房点检）：
- 端点：``POST /api/cps/room-checks/{id}/callback``
  - ``{id}`` = Java 侧点检单号（与 Python 侧 ``fingerprint`` 一一对应）；
- Body（按 fingerprint / overall / score / reasons 四字段）：
  ``{"fingerprint": str, "overall": "PASS"|"PARTIAL"|"PROBLEM",
  "score": int, "reasons": list[str]}``；
- 超时 30s，单调用失败 → 重试 2 次（共 3 次尝试，按 1s/3s 退避）；
- 投递成功 / 失败均计入审计日志（便于 Java 端对账）。

实现策略（与 ``app/projects/initial_review/infrastructure/callback.py``
JavaCallbackClient 严格对齐）：
- 每请求新建 ``httpx.AsyncClient``（简化实现；连接池待联调期评估）；
- 退避列表从配置读取（默认 ``(1.0, 3.0)`` 与 initial_review 一致）；
- 返回 ``(ok, attempts, last_error)`` 三元组——上层 service 记日志。

注意：本波次不要求调通 Java（联调清单），客户端按契约字段实现，
单测用 ``httpx.MockTransport`` 注入。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from app.projects.room_checks.domain.evidence_models import RoomCheckVerdict

logger = logging.getLogger("cps_agent.room_checks.callback")


#: 默认 endpoint 模板（Java 端点：``{id}`` 占位由 fingerprint 替换）
DEFAULT_ENDPOINT_TEMPLATE = (
    "http://java.test/api/cps/room-checks/{fingerprint}/callback"
)

#: 默认 timeout（30s，与任务书一致）
DEFAULT_TIMEOUT_SECONDS = 30.0

#: 默认重试配置（最多 2 次重试，共 3 次尝试）
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF = (1.0, 3.0)


@dataclass(frozen=True)
class CallbackOutcome:
    """回调投递结果（单测与日志共同观测）。"""

    ok: bool
    attempts: int
    last_error: str | None
    fingerprint: str


@runtime_checkable
class RoomCheckCallbackClient(Protocol):
    """B6 回调接口（单测以 ``FakeRoomCheckCallbackClient`` 替身满足）。"""

    async def dispatch(self, verdict: RoomCheckVerdict) -> CallbackOutcome:
        """投递判定结果到 Java 端。

        Args:
            verdict: 服务层三级判定产物。

        Returns:
            CallbackOutcome（成功 / 失败均可；失败时含 last_error 详情）。
        """
        ...


class JavaRoomCheckCallbackClient:
    """真实 Java 回调客户端（httpx + 退避重试）。

    设计取舍：
    - 不复用 ``app.services.llm.llm``（那是 OpenAI 协议）；Java 端是
      Spring Boot REST 控制器，独立栈更合适；
    - 不引入连接池（每请求新建 AsyncClient）——B6 提交频率远低于
      initial_review（每天点检批次级别），不值得优化；
    - 退避默认 1s/3s，与 initial_review 回调一致；避免阻塞高优提交。
    """

    def __init__(
        self,
        *,
        endpoint_template: str = DEFAULT_ENDPOINT_TEMPLATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint_template = endpoint_template
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, int(max_retries))
        self._backoff = tuple(backoff) if backoff else ()
        self._transport = transport

    def _build_url(self, fingerprint: str) -> str:
        # 防御：fingerprint 绝不允许 URL 模板注入
        if "{" in fingerprint or "}" in fingerprint or "/" in fingerprint:
            raise ValueError(
                f"fingerprint 含非法字符（fingerprint={fingerprint!r}）"
            )
        return self._endpoint_template.format(fingerprint=fingerprint)

    def _build_payload(self, verdict: RoomCheckVerdict) -> dict[str, Any]:
        return {
            "fingerprint": verdict.fingerprint,
            "overall": verdict.overall.value,
            "score": int(verdict.score),
            "reasons": list(verdict.reasons),
        }

    async def dispatch(self, verdict: RoomCheckVerdict) -> CallbackOutcome:
        url = self._build_url(verdict.fingerprint)
        payload = self._build_payload(verdict)
        max_attempts = 1 + self._max_retries
        last_error: str | None = None
        attempts = 0
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                for attempt in range(1, max_attempts + 1):
                    attempts = attempt
                    try:
                        resp = await client.post(url, json=payload)
                    except httpx.HTTPError as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                    else:
                        if 200 <= resp.status_code < 300:
                            logger.info(
                                "[B6 callback] dispatched ok fingerprint=%s attempt=%s",
                                verdict.fingerprint,
                                attempt,
                            )
                            return CallbackOutcome(
                                ok=True,
                                attempts=attempt,
                                last_error=None,
                                fingerprint=verdict.fingerprint,
                            )
                        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < max_attempts:
                        delay = (
                            self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                            if self._backoff
                            else 0.0
                        )
                        if delay > 0:
                            await asyncio.sleep(delay)
        except Exception as exc:  # noqa: BLE001 - 全捕获以免阻塞调用方
            # 理论不会到（AsyncClient 自身不会抛）——防御性
            last_error = f"{type(exc).__name__}: {exc}"
            attempts = max_attempts
        logger.warning(
            "[B6 callback] dispatch failed fingerprint=%s attempts=%s last_error=%s",
            verdict.fingerprint,
            attempts,
            last_error,
        )
        return CallbackOutcome(
            ok=False,
            attempts=attempts,
            last_error=last_error,
            fingerprint=verdict.fingerprint,
        )


class FakeRoomCheckCallbackClient:
    """测试替身：按脚本返回 CallbackOutcome 或抛错（不触网）。

    用法：
        client = FakeRoomCheckCallbackClient(scripts=[
            CallbackOutcome(ok=True, attempts=1, last_error=None, fingerprint="..."),
            CallbackOutcome(ok=False, attempts=2, last_error="simulated", fingerprint="..."),
        ])
        多次调用按顺序消费；缺省脚本（None）即视为 ``ok=True``。
    """

    def __init__(
        self,
        *,
        scripts: list[CallbackOutcome | Exception] | None = None,
    ) -> None:
        self.scripts = list(scripts or [])
        self.dispatches: list[RoomCheckVerdict] = []

    async def dispatch(self, verdict: RoomCheckVerdict) -> CallbackOutcome:
        self.dispatches.append(verdict)
        if not self.scripts:
            return CallbackOutcome(
                ok=True,
                attempts=1,
                last_error=None,
                fingerprint=verdict.fingerprint,
            )
        item = self.scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


__all__ = [
    "CallbackOutcome",
    "DEFAULT_BACKOFF",
    "DEFAULT_ENDPOINT_TEMPLATE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "FakeRoomCheckCallbackClient",
    "JavaRoomCheckCallbackClient",
    "RoomCheckCallbackClient",
]