"""A7/A8 模型客户端 + object_key 附件读取（RustFS S3 GET）。

设计口径（波次 2 设计文档 §2.1/§2.4）：
- 结构化输出：prompt 约定 JSON → 容错解析（剥代码围栏/截取首个 JSON 对象）→
  pydantic 校验；校验失败把错误回喂重试 1 次；两次无效 → ModelCallError
  （上层记 DEGRADED，不伪造内容结论）；
- 模型不可用（provider 未配置 / enabled=false）→ ModelUnavailableError
  （上层记 SKIPPED + 原因）；
- 复用全局 ``app.services.llm.llm`` 单例（LangChain ChatOpenAI，多 provider），
  单测注入 FakeModelCheckClient，真实冒烟走 scripts/wave2_real_model_smoke.py；
- RustFS：S3 SigV4 GET（httpx + stdlib hmac/sha256，零新增依赖；对齐 Java 侧
  MinIO SDK 同一凭据体系，endpoint 默认 http://127.0.0.1:9000）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from app.projects.initial_review.infrastructure.config import (
    ModelCheckSettings,
    RustFSSettings,
)

logger = logging.getLogger(__name__)


class ModelUnavailableError(Exception):
    """模型不可用（未配置/显式禁用）→ 检查 SKIPPED（前置条件缺失，非故障）。"""


class ModelCallError(Exception):
    """模型调用技术失败（超时由上层 asyncio.timeout 判定/网络异常/输出两次无效）。"""


@runtime_checkable
class ModelCheckClient(Protocol):
    """A7/A8 所需的最小模型接口（单测以 Fake 替身满足）。"""

    async def complete_text_json(
        self, prompt: str, schema: type[BaseModel]
    ) -> BaseModel:
        """文本模型调用并返回校验后的结构化输出。"""
        ...

    async def complete_vision_json(
        self, prompt: str, image_data_urls: list[str], schema: type[BaseModel]
    ) -> BaseModel:
        """多模态（图+文）调用并返回校验后的结构化输出。"""
        ...


#: 截取输出长度上限（错误信息防膨胀）
_ERROR_SNIPPET = 200


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出容错提取首个 JSON 对象（剥 ```json 围栏 / 前后杂讯）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中未找到 JSON 对象")
    obj = json.loads(cleaned[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("输出不是 JSON 对象")
    return obj


class DashScopeModelClient:
    """经 ``app.services.llm.llm`` 单例调用 qwen 系模型（OpenAI 兼容模式）。

    惰性取单例（import 时 llm 可能尚未 startup）；provider 未配置即视为不可用。
    """

    def __init__(self, cfg: ModelCheckSettings):
        self._cfg = cfg

    def _llm(self):
        from app.services.llm import llm  # 惰性导入：避免模块级依赖测试环境 startup

        if not self._cfg.enabled:
            raise ModelUnavailableError("模型检查已显式禁用（model_check.enabled=false）")
        if not llm.providers:
            raise ModelUnavailableError(
                "未配置任何 LLM provider（llm.providers 为空），模型检查不可用"
            )
        if self._cfg.provider not in llm.providers:
            raise ModelUnavailableError(
                f"LLM provider '{self._cfg.provider}' 未配置。可用：{llm.providers}"
            )
        return llm

    async def _invoke_json(
        self,
        messages: list[Any],
        *,
        model: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        llm = self._llm()
        last_error = "unknown"
        for attempt in (1, 2):
            try:
                text = await llm.invoke(
                    messages,
                    provider=self._cfg.provider,
                    model=model,
                    temperature=self._cfg.temperature,
                )
            except ModelUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络/供应商故障统一技术失败
                raise ModelCallError(f"模型调用异常：{type(exc).__name__}: {exc}") from exc
            try:
                return schema.model_validate(extract_json_object(text))
            except (ValueError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:_ERROR_SNIPPET]}"
                logger.warning(
                    "structured output invalid (attempt %d/2): %s", attempt, last_error
                )
                # 回喂错误让模型自纠一次
                messages = messages + [
                    {"role": "assistant", "content": text[:_ERROR_SNIPPET * 2]},
                    {
                        "role": "user",
                        "content": (
                            f"你上一条输出不是合法的 {schema.__name__} JSON：{last_error}。"
                            "请只输出一个符合 schema 的 JSON 对象，不要任何其他文字。"
                        ),
                    },
                ]
        raise ModelCallError(f"模型输出两次无效（{schema.__name__}）：{last_error}")

    async def complete_text_json(
        self, prompt: str, schema: type[BaseModel]
    ) -> BaseModel:
        return await self._invoke_json(
            [{"role": "user", "content": prompt}],
            model=self._cfg.text_model,
            schema=schema,
        )

    async def complete_vision_json(
        self, prompt: str, image_data_urls: list[str], schema: type[BaseModel]
    ) -> BaseModel:
        if not image_data_urls:
            raise ModelCallError("视觉调用缺少图片输入")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return await self._invoke_json(
            [{"role": "user", "content": content}],
            model=self._cfg.vision_model,
            schema=schema,
        )


class FakeModelCheckClient:
    """测试替身：按预设脚本返回/抛错（不触网）。"""

    def __init__(
        self,
        *,
        results: list[BaseModel | Exception] | None = None,
        unavailable: str | None = None,
    ) -> None:
        self.results = list(results or [])
        self.calls: list[dict[str, Any]] = []
        self.unavailable = unavailable

    async def complete_text_json(
        self, prompt: str, schema: type[BaseModel]
    ) -> BaseModel:
        return self._next("text", prompt, schema)

    async def complete_vision_json(
        self, prompt: str, image_data_urls: list[str], schema: type[BaseModel]
    ) -> BaseModel:
        return self._next("vision", prompt, schema, image_data_urls=image_data_urls)

    def _next(
        self,
        kind: str,
        prompt: str,
        schema: type[BaseModel],
        *,
        image_data_urls: list[str] | None = None,
    ) -> BaseModel:
        self.calls.append(
            {"kind": kind, "prompt": prompt, "schema": schema.__name__,
             "images": list(image_data_urls or [])}
        )
        if self.unavailable:
            raise ModelUnavailableError(self.unavailable)
        if not self.results:
            raise AssertionError("FakeModelCheckClient 预设结果耗尽")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --------------------------------------------------------------------------
# RustFS（S3 兼容）object_key → bytes/data URL
# --------------------------------------------------------------------------

#: 附件后缀 → MIME（base64 data URL 组装用；pipeline 复用）
MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _sign_key(secret: str, date: str, region: str, service: str) -> bytes:
    """AWS SigV4 派生密钥（RustFS 兼容 MinIO，region 任意非空值）。"""
    k = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


class RustFSObjectFetcher:
    """S3 SigV4 GET（path-style：{endpoint}/{bucket}/{object_key}）。"""

    def __init__(
        self,
        cfg: RustFSSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._cfg = cfg
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._region = region

    async def fetch_bytes(self, object_key: str) -> bytes:
        reason = self._cfg.unavailable_reason
        if reason:
            raise ModelUnavailableError(f"object_key 附件无法获取：{reason}")
        parsed = httpx.URL(self._cfg.endpoint)
        path = f"/{self._cfg.bucket}/{object_key.lstrip('/')}"
        url = parsed.copy_with(path=path)
        now = datetime.now(UTC)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_headers = (
            f"host:{url.netloc.decode()}\nx-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            ["GET", path, "", canonical_headers, signed_headers, payload_hash]
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
        signature = hmac.new(
            key, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self._cfg.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.get(
                str(url),
                headers={
                    "Authorization": authorization,
                    "x-amz-date": amz_date,
                    "x-amz-content-sha256": payload_hash,
                },
                timeout=30.0,
            )
        if resp.status_code != 200:
            raise ModelCallError(
                f"RustFS GET {object_key} → HTTP {resp.status_code}: {resp.text[:120]}"
            )
        return resp.content

    async def fetch_data_url(self, object_key: str) -> str:
        """object_key → data URL（送视觉模型的统一形态）。"""
        data = await self.fetch_bytes(object_key)
        suffix = "." + object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
        mime = MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


__all__ = [
    "DashScopeModelClient",
    "FakeModelCheckClient",
    "ModelCallError",
    "ModelCheckClient",
    "ModelUnavailableError",
    "RustFSObjectFetcher",
    "extract_json_object",
]
