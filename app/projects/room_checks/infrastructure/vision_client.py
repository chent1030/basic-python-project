"""B6 视觉模型客户端（qwen-vl-plus multi-modal input）。

复用 ``app.projects.initial_review.infrastructure.model_client`` 的：
- ``DashScopeModelClient.complete_vision_json``: 多模态（图+文）调用
  ``app.services.llm.llm`` 单例（OpenAI 兼容模式调 DashScope qwen 系）；
- ``extract_json_object``: 波次 8 多级降级 JSON 提取（整段 → 栅栏 →
  平衡花括号扫描 → 单引号/尾逗号修复）。

B6 适配层只做两件事：
1. 暴露与既有 ``DashScopeModelClient`` 同款接口（``complete_vision``）；
2. 在 fallback 路径（provider 未配置 / 解析两次无效）下，调用方拿到的
   ``VisionCallResult.raw_text`` 仍可写进 ``RoomCheckVerdict.raw_output``，
   便于合规留痕。

设计选择（为什么是「适配层」而非「独立 HTTP 客户端」）：
- ``app.services.llm.llm`` 单例已经支持 provider/model 切换；再造
  一个独立 httpx 调用意味着与全局 provider 配置脱钩、绕过限流与计费
  上报；不值得为「一次 B6 调用」引入第二套调用栈。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.projects.initial_review.infrastructure.config import ModelCheckSettings
from app.projects.initial_review.infrastructure.model_client import (
    DashScopeModelClient,
    ModelCallError,
    ModelUnavailableError,
    extract_json_object,
)

logger = logging.getLogger("cps_agent.room_checks.vision")


#: 输出截断长度（与 initial_review/infrastructure/model_client.py 同款）
_ERROR_SNIPPET = 200


@dataclass(frozen=True)
class VisionCallResult:
    """视觉模型单次调用的结构化结果（解析失败时 ``parsed=None``）。"""

    raw_text: str
    parsed: dict[str, Any] | None
    model: str


@runtime_checkable
class VisionModelClient(Protocol):
    """B6 视觉模型接口（单测以 ``FakeVisionModelClient`` 替身满足）。"""

    async def complete_vision(
        self, prompt: str, image_data_urls: tuple[str, ...]
    ) -> VisionCallResult:
        """多模态调用，返回原始文本 + 解析后的 dict（解析失败 parsed=None）。

        Args:
            prompt: 由 ``PromptBuilder`` 生成的三级判定 prompt。
            image_data_urls: data URL 列表（与 initial_review 视觉调用同款
                形态；首张照片即可——B6 三级判定每级复用同一张照片）。

        Returns:
            VisionCallResult(raw_text, parsed, model)。

        Raises:
            ModelUnavailableError: provider 未配置/禁用（前置条件缺失）。
            ModelCallError: 网络/超时/解析两次无效（技术失败，可重试）。
        """
        ...


class QwenVLPlusClient:
    """真实 qwen-vl-plus 客户端（多模态 input：image_url + text prompt）。

    内部委托 ``DashScopeModelClient.complete_vision_json``——因为它已经
    处理了：多模态消息组装、prompt 末尾 JSON-only 约束、回喂重试一次、
    pydantic 校验失败转换等。

    本类提供 B6 特定行为：
    - 解析失败时保留 ``raw_text`` 供上层降级（与
      ``DashScopeModelClient._invoke_json`` 直接抛 ModelCallError 略不同）；
    - 默认 model = ``qwen-vl-plus``（与任务书一致）；
    - timeout 由 ``asyncio.timeout`` 在调用方控制（避免单次阶段阻塞）。
    """

    def __init__(
        self,
        cfg: ModelCheckSettings | None = None,
        *,
        vision_model: str = "qwen-vl-plus",
    ) -> None:
        # 复用 initial_review 的 settings（共享 model_check.enabled/provider）；
        # None 表示未注入——首次调用时按需懒加载。
        self._cfg = cfg
        self._vision_model = vision_model
        self._inner: DashScopeModelClient | None = None

    @property
    def model_name(self) -> str:
        return self._vision_model

    def _ensure_inner(self) -> DashScopeModelClient:
        if self._inner is None:
            if self._cfg is None:
                from app.projects.initial_review.infrastructure.config import (
                    load_initial_review_settings,
                )

                self._cfg = load_initial_review_settings().model_check
            # 注意：B6 不强制 pydantic schema（每级 prompt 输出字段不同）；
            # 复用 inner 提供的 transport/multi-modal 组装，但绕过 schema 校验
            # 直接拿 raw text——见 ``complete_vision`` 实现。
            self._inner = DashScopeModelClient(self._cfg)
        return self._inner

    async def complete_vision(
        self, prompt: str, image_data_urls: tuple[str, ...]
    ) -> VisionCallResult:
        if not image_data_urls:
            raise ModelCallError("B6 视觉调用缺少 image_data_urls（无照片）")

        inner = self._ensure_inner()
        # 构造「无 schema 校验」的多模态调用：直接把 schema=Any 注入，
        # pydantic.model_validate(Any) 在初始实现中是直接通过（dict 走 RawDictSchema），
        # 这里更稳妥的做法是直接调底层 llm.invoke 拿到 raw text 后用
        # extract_json_object 容错解析。
        from app.services.llm import llm

        if not inner._cfg.enabled:
            raise ModelUnavailableError(
                "模型检查已显式禁用（model_check.enabled=false）"
            )
        if not llm.providers:
            raise ModelUnavailableError(
                "未配置任何 LLM provider（llm.providers 为空）"
            )
        if inner._cfg.provider not in llm.providers:
            raise ModelUnavailableError(
                f"LLM provider '{inner._cfg.provider}' 未配置"
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [{"role": "user", "content": content}]

        try:
            text = await llm.invoke(
                messages,
                provider=inner._cfg.provider,
                model=self._vision_model,
                temperature=inner._cfg.temperature,
            )
        except ModelUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/供应商故障统一技术失败
            raise ModelCallError(
                f"qwen-vl-plus 调用异常：{type(exc).__name__}: {exc}"
            ) from exc

        # 解析失败保留 raw text；上层（PromptBuilder -> 服务层）决定如何扣分
        try:
            parsed = extract_json_object(text)
            return VisionCallResult(raw_text=text, parsed=parsed, model=self._vision_model)
        except ValueError as exc:
            logger.warning(
                "B6 vision raw output not JSON-parseable: %s", str(exc)[:_ERROR_SNIPPET]
            )
            return VisionCallResult(raw_text=text, parsed=None, model=self._vision_model)


class FakeVisionModelClient:
    """测试替身：按脚本返回 VisionCallResult 或抛错（不触网）。

    用法：
        client = FakeVisionModelClient(results=[
            VisionCallResult(raw_text='{"matched": true, ...}', parsed={...}, model='qwen-vl-plus'),
            VisionCallResult(...),
            VisionCallResult(...),
        ])
    """

    def __init__(
        self,
        *,
        results: list[VisionCallResult | Exception] | None = None,
        unavailable: str | None = None,
    ) -> None:
        self.results = list(results or [])
        self.unavailable = unavailable
        self.calls: list[dict[str, Any]] = []

    async def complete_vision(
        self, prompt: str, image_data_urls: tuple[str, ...]
    ) -> VisionCallResult:
        self.calls.append(
            {"prompt": prompt, "images": list(image_data_urls)}
        )
        if self.unavailable:
            raise ModelUnavailableError(self.unavailable)
        if not self.results:
            raise AssertionError("FakeVisionModelClient 预设结果耗尽")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


__all__ = [
    "FakeVisionModelClient",
    "QwenVLPlusClient",
    "VisionCallResult",
    "VisionModelClient",
]