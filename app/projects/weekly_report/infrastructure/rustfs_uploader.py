"""周报归档上传（RustFS S3 SigV4 PUT；零新增依赖）。

复用 A7 / initial_review 同套 SigV4 签名算法（httpx + stdlib hmac/sha256），
本模块为周报归档提供：
- :func:`put_bytes`：上传对象 + 返回 (etag, size)；
- :func:`stream_get`：从 RustFS 流式 GET（C-08 受控下载用）。

设计 §4 路径 ``weekly-reports/{type}/{yyyy-Ww}/{run_no}.html`` 天然版本化：
重跑不覆盖（run_no 自增）。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from app.projects.weekly_report.infrastructure.config import (
    WeeklyReportRustFSSettings,
)

logger = logging.getLogger(__name__)


class RustFSUnavailableError(Exception):
    """RustFS 配置不完整 / 不可达（不伪造「已归档」语义）。"""


class RustFSPutError(Exception):
    """RustFS PUT 失败（HTTP 非 2xx / 鉴权失败 / 网络异常）。"""


def _sign_key(secret: str, date: str, region: str, service: str) -> bytes:
    """AWS SigV4 派生密钥（与 initial_review 同源；region 任意非空值）。"""
    k = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _put_headers(
    cfg: WeeklyReportRustFSSettings,
    *,
    url: httpx.URL,
    body_sha256: str,
    body_bytes: bytes,
    content_type: str,
) -> dict[str, str]:
    """组装 PUT 请求所需 SigV4 头（与 GET 同源：host/x-amz-content-sha256/x-amz-date）。"""
    now = datetime.now(UTC)
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    region = "us-east-1"
    canonical_headers = (
        f"host:{url.netloc.decode()}\n"
        f"x-amz-content-sha256:{body_sha256}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "PUT",
            url.path,
            "",
            canonical_headers,
            signed_headers,
            body_sha256,
        ]
    )
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    key = _sign_key(cfg.secret_key, datestamp, region, "s3")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={cfg.access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": body_sha256,
        "Content-Type": content_type,
        "Content-Length": str(len(body_bytes)),
    }


class RustFSWeeklyReportUploader:
    """周报归档 RustFS PUT + 受控 GET。"""

    def __init__(
        self,
        cfg: WeeklyReportRustFSSettings,
        *,
        region: str = "us-east-1",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._region = region
        self._transport = transport  # 测试 MockTransport 注入

    @property
    def available(self) -> bool:
        return self._cfg.unavailable_reason is None

    @property
    def unavailable_reason(self) -> str | None:
        return self._cfg.unavailable_reason

    async def put_bytes(
        self,
        *,
        object_key: str,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
    ) -> tuple[str | None, int]:
        """PUT 字节；返回 ``(etag, size)``。失败抛 RustFSPutError 或
        RustFSUnavailableError（不可用语义 — 调用方应当转为 FAILED）。"""
        if not self.available:
            raise RustFSUnavailableError(self._cfg.unavailable_reason or "")
        parsed = httpx.URL(self._cfg.endpoint)
        path = f"/{self._cfg.bucket}/{object_key.lstrip('/')}"
        url = parsed.copy_with(path=path)
        body_sha256 = hashlib.sha256(body).hexdigest()
        headers = _put_headers(
            self._cfg,
            url=url,
            body_sha256=body_sha256,
            body_bytes=body,
            content_type=content_type,
        )
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.put(
                str(url), content=body, headers=headers, timeout=30.0
            )
        if resp.status_code not in (200, 201):
            raise RustFSPutError(
                f"RustFS PUT {object_key} → HTTP {resp.status_code}: {resp.text[:200]}"
            )
        # ETag 通常在响应头；也可能没有（如 chunked 编码）
        etag = resp.headers.get("ETag") or resp.headers.get("etag")
        if etag is not None:
            etag = etag.strip('"').strip()
        return etag, len(body)

    async def stream_get(
        self, object_key: str
    ) -> AsyncIterator[bytes]:
        """流式 GET（C-08 受控下载用，按 chunk 产出字节）。"""
        if not self.available:
            raise RustFSUnavailableError(self._cfg.unavailable_reason or "")
        parsed = httpx.URL(self._cfg.endpoint)
        path = f"/{self._cfg.bucket}/{object_key.lstrip('/')}"
        url = parsed.copy_with(path=path)
        now = datetime.now(UTC)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        body_sha256 = hashlib.sha256(b"").hexdigest()
        canonical_headers = (
            f"host:{url.netloc.decode()}\n"
            f"x-amz-content-sha256:{body_sha256}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            ["GET", path, "", canonical_headers, signed_headers, body_sha256]
        )
        scope = f"{datestamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        key = _sign_key(self._cfg.secret_key, datestamp, self._region, "s3")
        signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self._cfg.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        async with httpx.AsyncClient(transport=self._transport) as client:
            async with client.stream(
                "GET",
                str(url),
                headers={
                    "Authorization": authorization,
                    "x-amz-date": amz_date,
                    "x-amz-content-sha256": body_sha256,
                },
                timeout=60.0,
            ) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    raise RustFSPutError(
                        f"RustFS GET {object_key} → HTTP {resp.status_code}: "
                        f"{text[:200]!r}"
                    )
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    yield chunk


__all__ = [
    "RustFSPutError",
    "RustFSUnavailableError",
    "RustFSWeeklyReportUploader",
]