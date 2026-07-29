"""LLM module — LangChain + 多 provider(NewAPI 兼容端点)。

支持配置多个供应商(多个 NewAPI 地址),调用时通过 provider 参数切换。
不传 provider 则用 config 的 default_provider。

为什么 LangChain + ChatOpenAI 适合「国产模型 via NewAPI」:
- ChatOpenAI 说 OpenAI chat.completions 协议,这正是 NewAPI 对外暴露的协议,
  无论后端是 DeepSeek / Qwen / GLM / Kimi 都兼容。
- 我们保留自己的 YAML/Jinja2 prompt 系统(比 LangChain 的 PromptTemplate 更灵活),
  LangChain 只用于模型调用 + LCEL 链式编排。
- 只发送国产模型都支持的最小参数集。NewAPI 会拒绝 OpenAI 专属参数
  (reasoning_effort / service_tier / logprobs 等),不要加。

Public API:
- `llm.invoke(messages, provider=...)`           单次调用
- `llm.complete_prompt(name, provider=..., **vars)`   按 prompt 文件调用
- `llm.complete_prompt_stream(...)`              流式版
- `llm.chain(template_name, provider=...)`       LCEL 链(多步骤编排)
- `llm.use(provider)`                            返回绑定某 provider 的子服务

Usage:
    from app.services.llm import llm

    # 1) 用默认 provider
    text = await llm.complete_prompt("translate", source="Hi", target_lang="中文")

    # 2) 切换 provider
    text = await llm.complete_prompt(
        "translate", source="Hi", target_lang="中文", provider="qwen"
    )

    # 3) 链式:某流程里固定用某 provider
    qwen_llm = llm.use("qwen")
    await qwen_llm.complete_prompt("summarize", text=article)

    # 4) 多步骤编排(LCEL 管道)
    pipe = llm.chain("summarize", output_key="source") | llm.chain("translate")
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI

from app.core.config import LLMProviderConfig, settings
from app.core.prompt import RenderedPrompt, render_prompt

log = logging.getLogger("app.llm")


# --------------------------------------------------------------------------
# Helper: render our prompt files into LangChain messages
# --------------------------------------------------------------------------
def _to_lc_messages(rendered: RenderedPrompt) -> list[BaseMessage]:
    """Translate our RenderedPrompt into a list of LangChain messages."""
    msgs: list[BaseMessage] = []
    if rendered.system:
        msgs.append(SystemMessage(content=rendered.system))
    if rendered.user:
        msgs.append(HumanMessage(content=rendered.user))
    return msgs


# --------------------------------------------------------------------------
# LLM service — 多 provider 管理
# --------------------------------------------------------------------------
class LLMService:
    """管理多个 provider 的 ChatModel,按需切换。

    启动时为每个 provider 建一个 ChatOpenAI(缓存),调用时按 provider 参数取。
    不传 provider 用 settings.llm.default_provider。
    """

    def __init__(self) -> None:
        # provider name -> ChatModel
        self._models: dict[str, BaseChatModel] = {}

    # ---------- lifecycle ---------------------------------------------
    async def startup(self) -> None:
        cfg = settings.llm
        for name, prov in cfg.providers.items():
            self._models[name] = _build_chat_model(name, prov)
        if not self._models:
            log.warning("未配置任何 LLM provider,LLM 功能不可用")
        else:
            log.info(
                "LLM ready: %d providers %s, default=%s",
                len(self._models),
                list(self._models),
                cfg.default_provider,
            )

    async def shutdown(self) -> None:
        # ChatOpenAI 没有 async close hook,交给 GC
        self._models.clear()

    # ---------- provider 解析 -----------------------------------------
    def _resolve_provider(self, provider: str | None) -> str:
        """把 None 解析成 default_provider;校验存在性。"""
        name = provider or settings.llm.default_provider
        if name not in self._models:
            available = list(self._models) or "(无)"
            raise RuntimeError(
                f"LLM provider '{name}' 未配置。可用: {available}。"
                f"请在 config 的 llm.providers 下添加,或检查 default_provider。"
            )
        return name

    def _get_model(self, provider: str | None) -> BaseChatModel:
        name = self._resolve_provider(provider)
        return self._models[name]

    @property
    def providers(self) -> list[str]:
        """所有已配置的 provider 名。"""
        return list(self._models)

    # ---------- low-level: invoke a list of messages ------------------
    async def invoke(
        self,
        messages: list[BaseMessage] | list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        **model_kwargs: Any,
    ) -> str:
        """Invoke the model with messages, return the text content."""
        m = self._bound(provider, model=model, temperature=temperature, **model_kwargs)
        result = await m.ainvoke(_coerce_messages(messages))
        return _extract_text(result)

    async def invoke_stream(
        self,
        messages: list[BaseMessage] | list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        **model_kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming variant — yields text delta chunks."""
        m = self._bound(provider, model=model, temperature=temperature, **model_kwargs)
        async for chunk in m.astream(_coerce_messages(messages)):
            text = _extract_text(chunk)
            if text:
                yield text

    # ---------- prompt-driven helpers ---------------------------------
    async def complete_prompt(
        self,
        name: str,
        *,
        provider: str | None = None,
        refresh: bool = False,
        overrides: dict[str, Any] | None = None,
        **variables: Any,
    ) -> str:
        """Load + render a prompt file, then invoke the model."""
        rendered = render_prompt(name, refresh=refresh, **variables)
        return await self.invoke(
            _to_lc_messages(rendered),
            provider=provider,
            **_merge_overrides(rendered, overrides),
        )

    async def complete_prompt_stream(
        self,
        name: str,
        *,
        provider: str | None = None,
        refresh: bool = False,
        overrides: dict[str, Any] | None = None,
        **variables: Any,
    ) -> AsyncIterator[str]:
        """Streaming variant of `complete_prompt`."""
        rendered = render_prompt(name, refresh=refresh, **variables)
        async for chunk in self.invoke_stream(
            _to_lc_messages(rendered),
            provider=provider,
            **_merge_overrides(rendered, overrides),
        ):
            yield chunk

    # ---------- LCEL chain builder ------------------------------------
    def chain(
        self,
        prompt_name: str,
        *,
        provider: str | None = None,
        output_key: str | None = None,
    ) -> Runnable:
        """Build a reusable LCEL chain for a prompt file.

        Args:
            prompt_name: Prompt file name (without extension).
            provider:    用哪个 provider(默认用 default_provider)。
            output_key:  若给定,输出包成 {output_key: str} 喂给下一步。

        Example:
            single = llm.chain("translate", provider="qwen")
            text = await single.ainvoke({"source": "Hi", "target_lang": "中文"})

            # Pipeline: summarize -> translate
            pipe = llm.chain("summarize", output_key="source") | llm.chain("translate")
        """
        model = self._get_model(provider)

        def _render(variables: dict[str, Any]) -> list[BaseMessage]:
            rendered = render_prompt(prompt_name, **variables)
            return _to_lc_messages(rendered)

        core_chain: Runnable = RunnableLambda(_render) | model | StrOutputParser()

        if output_key is None:
            return core_chain

        def _wrap(result: str) -> dict[str, str]:
            return {output_key: result}

        return core_chain | RunnableLambda(_wrap)

    # ---------- 链式:绑定 provider ------------------------------------
    def use(self, provider: str) -> BoundLLM:
        """返回一个绑定到指定 provider 的子服务,后续调用都用该 provider。

        适合某个业务流程里固定用某个 provider 的场景:
            qwen = llm.use("qwen")
            await qwen.complete_prompt("translate", source="Hi")
            await qwen.complete_prompt("summarize", text=article)
        """
        # 提前校验 provider 存在(快速失败)
        self._resolve_provider(provider)
        return BoundLLM(self, provider)

    # ---------- internal ----------------------------------------------
    def _bound(
        self,
        provider: str | None,
        *,
        model: str | None,
        temperature: float | None,
        **kwargs: Any,
    ) -> BaseChatModel:
        m = self._get_model(provider)
        binds: dict[str, Any] = {}
        if model is not None:
            binds["model"] = model
        if temperature is not None:
            binds["temperature"] = temperature
        binds.update({k: v for k, v in kwargs.items() if v is not None})
        return m.bind(**binds) if binds else m  # type: ignore[return-value]


# --------------------------------------------------------------------------
# BoundLLM:绑定 provider 后的子服务
# --------------------------------------------------------------------------
class BoundLLM:
    """绑定到某个 provider 的 LLM 子服务。由 llm.use("xxx") 创建。"""

    def __init__(self, parent: LLMService, provider: str) -> None:
        self._parent = parent
        self._provider = provider

    async def invoke(self, messages: list, **kwargs: Any) -> str:
        return await self._parent.invoke(messages, provider=self._provider, **kwargs)

    async def invoke_stream(self, messages: list, **kwargs: Any) -> AsyncIterator[str]:
        async for chunk in self._parent.invoke_stream(
            messages, provider=self._provider, **kwargs
        ):
            yield chunk

    async def complete_prompt(self, name: str, **kwargs: Any) -> str:
        return await self._parent.complete_prompt(name, provider=self._provider, **kwargs)

    async def complete_prompt_stream(self, name: str, **kwargs: Any) -> AsyncIterator[str]:
        async for chunk in self._parent.complete_prompt_stream(
            name, provider=self._provider, **kwargs
        ):
            yield chunk

    def chain(self, prompt_name: str, **kwargs: Any) -> Runnable:
        return self._parent.chain(prompt_name, provider=self._provider, **kwargs)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _build_chat_model(name: str, cfg: LLMProviderConfig) -> BaseChatModel:
    """根据 provider 配置构造一个 ChatOpenAI。"""
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "temperature": cfg.temperature,
        "timeout": cfg.timeout,
        "max_retries": cfg.max_retries,
    }
    if cfg.max_tokens:
        kwargs["max_tokens"] = cfg.max_tokens
    return ChatOpenAI(**kwargs)


def _coerce_messages(
    messages: list[BaseMessage] | list[dict[str, str]],
) -> list[BaseMessage]:
    """Accept either LangChain messages or plain dicts; return LC messages."""
    out: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            out.append(msg)
        elif isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role == "assistant":
                out.append(AIMessage(content=content))
            else:
                out.append(HumanMessage(content=content))
        else:
            raise TypeError(f"Unsupported message type: {type(msg)}")
    return out


def _extract_text(result: Any) -> str:
    """Pull text content from an AIMessage / chunk."""
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
            return "".join(parts)
    return str(result)


def _merge_overrides(
    rendered: RenderedPrompt, overrides: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge prompt-file defaults with caller overrides.

    Priority: explicit overrides > prompt file > config defaults.
    """
    ov = overrides or {}
    out: dict[str, Any] = {}
    if ov.get("model") is not None or rendered.model is not None:
        out["model"] = ov.get("model") or rendered.model
    if ov.get("temperature") is not None or rendered.temperature is not None:
        out["temperature"] = ov.get("temperature", rendered.temperature)
    if ov.get("max_tokens") is not None or rendered.max_tokens is not None:
        out["max_tokens"] = ov.get("max_tokens", rendered.max_tokens)
    return out


# Singleton — started/stopped from app lifespan.
llm = LLMService()


__all__ = ["LLMService", "BoundLLM", "llm", "RenderedPrompt"]
