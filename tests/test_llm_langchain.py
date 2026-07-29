"""Tests for the LangChain LLM module.

We use LangChain's built-in `FakeListChatModel` so the whole LCEL stack
(prompt render -> model -> parser) runs for real, just without hitting
any API. This is more faithful than mocking individual methods.
"""
from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.services.llm import LLMService, _coerce_messages, _extract_text


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def test_coerce_messages_from_dicts():
    msgs = _coerce_messages(
        [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert isinstance(msgs[2], AIMessage)
    assert msgs[1].content == "hi"


def test_coerce_messages_passthrough_langchain_objects():
    raw = [HumanMessage(content="yo")]
    assert _coerce_messages(raw) is not raw
    assert _coerce_messages(raw)[0].content == "yo"


def test_extract_text_from_ai_message():
    assert _extract_text(AIMessage(content="hello")) == "hello"


def test_extract_text_from_string():
    assert _extract_text("plain") == "plain"


def test_extract_text_from_block_list():
    # Some providers return list-of-blocks
    assert _extract_text(
        AIMessage(content=[{"type": "text", "text": "a"}, {"text": "b"}])
    ) == "ab"


# ----------------------------------------------------------------------
# Test fixture: build an LLMService with one or more fake providers
# ----------------------------------------------------------------------
def _make_fake_model(*responses: str) -> FakeListChatModel:
    """A ChatModel that returns the given strings in order (cycling if exhausted)."""
    return FakeListChatModel(responses=list(responses))


def _svc_with_default(*responses: str, provider: str = "default") -> LLMService:
    """构建一个带单个 fake provider 的 LLMService,并把 default_provider 设成它。"""
    from app.core.config import settings

    settings.llm.default_provider = provider
    svc = LLMService()
    svc._models[provider] = _make_fake_model(*responses)
    return svc


# ----------------------------------------------------------------------
# LLMService.invoke — FakeListChatModel makes the whole stack real
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoke_returns_text():
    svc = _svc_with_default("translated!")
    text = await svc.invoke([{"role": "user", "content": "hi"}])
    assert text == "translated!"


@pytest.mark.asyncio
async def test_complete_prompt_renders_variables():
    """complete_prompt should render the prompt file then call the model."""
    svc = _svc_with_default("TRANSLATED")
    text = await svc.complete_prompt("translate", source="Hello", target_lang="中文")
    assert text == "TRANSLATED"


@pytest.mark.asyncio
async def test_complete_prompt_missing_variable_raises():
    svc = _svc_with_default("x")
    # source is required in translate.yaml
    with pytest.raises(ValueError, match="missing required variables"):
        await svc.complete_prompt("translate", target_lang="中文")


@pytest.mark.asyncio
async def test_invoke_raises_when_no_provider():
    """未配置任何 provider 时调用应报错。"""
    from app.core.config import settings

    settings.llm.default_provider = "missing"
    svc = LLMService()  # 空 _models
    with pytest.raises(RuntimeError, match="未配置"):
        await svc.invoke([HumanMessage(content="x")])


# ----------------------------------------------------------------------
# Multi-provider switching
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoke_with_explicit_provider():
    """传 provider 参数应切到对应 provider 的模型。"""
    svc = LLMService()
    svc._models["deepseek"] = _make_fake_model("from-deepseek")
    svc._models["qwen"] = _make_fake_model("from-qwen")

    assert await svc.invoke([HumanMessage(content="x")], provider="deepseek") == "from-deepseek"
    assert await svc.invoke([HumanMessage(content="x")], provider="qwen") == "from-qwen"


@pytest.mark.asyncio
async def test_use_returns_bound_provider():
    """llm.use('qwen') 返回的子服务应固定用 qwen。"""
    svc = LLMService()
    svc._models["qwen"] = _make_fake_model("qwen-1", "qwen-2")
    svc._models["deepseek"] = _make_fake_model("ds-1")

    qwen = svc.use("qwen")
    assert await qwen.invoke([HumanMessage(content="x")]) == "qwen-1"
    assert await qwen.invoke([HumanMessage(content="x")]) == "qwen-2"

    # 直接调 svc 仍可切其他 provider
    assert await svc.invoke([HumanMessage(content="x")], provider="deepseek") == "ds-1"


@pytest.mark.asyncio
async def test_use_unknown_provider_raises():
    svc = LLMService()
    svc._models["a"] = _make_fake_model("x")
    with pytest.raises(RuntimeError, match="未配置"):
        svc.use("nonexistent")


@pytest.mark.asyncio
async def test_providers_property_lists_names():
    svc = LLMService()
    svc._models["a"] = _make_fake_model("x")
    svc._models["b"] = _make_fake_model("y")
    assert set(svc.providers) == {"a", "b"}


# ----------------------------------------------------------------------
# LCEL chain
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chain_single_step():
    svc = _svc_with_default("step-result")
    # joke.txt requires only `topic`
    chain = svc.chain("joke")
    result = await chain.ainvoke({"topic": "程序员"})
    assert result == "step-result"


@pytest.mark.asyncio
async def test_chain_with_output_key_wraps_result():
    svc = _svc_with_default("summary")
    # summarize.j2 needs text, max_points, lang
    chain = svc.chain("summarize", output_key="text")
    result = await chain.ainvoke({"text": "long article", "max_points": 2, "lang": "中文"})
    assert result == {"text": "summary"}


@pytest.mark.asyncio
async def test_chain_with_explicit_provider():
    """chain 应支持 provider 参数。"""
    svc = LLMService()
    svc._models["qwen"] = _make_fake_model("qwen-chain")
    chain = svc.chain("joke", provider="qwen")
    result = await chain.ainvoke({"topic": "cat"})
    assert result == "qwen-chain"


@pytest.mark.asyncio
async def test_chain_pipeline_compose():
    """Two chains composed via | — output_key must match next prompt's variable."""
    svc = _svc_with_default("summary text", "TRANSLATION")
    # Step 1 summarize -> {source: ...}; Step 2 translate consumes `source`
    pipe = svc.chain("summarize", output_key="source") | svc.chain("translate")
    result = await pipe.ainvoke(
        {
            "text": "long article",
            "max_points": 2,
            "lang": "中文",
            "target_lang": "English",
        }
    )
    assert result == "TRANSLATION"


@pytest.mark.asyncio
async def test_chain_streaming():
    """Streaming a chain should yield text chunks."""
    svc = _svc_with_default("STREAMED-OUTPUT")
    chain = svc.chain("joke")
    chunks = [c async for c in chain.astream({"topic": "cat"})]
    # FakeListChatModel returns the whole response as one chunk
    assert "".join(chunks) == "STREAMED-OUTPUT"


# ----------------------------------------------------------------------
# Streaming wrapper
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoke_stream_yields_non_empty_chunks():
    svc = _svc_with_default("HELLO WORLD")
    chunks = [c async for c in svc.invoke_stream([HumanMessage(content="x")])]
    joined = "".join(chunks)
    assert "HELLO WORLD" in joined
    # No empty strings leaked through
    assert all(c for c in chunks)


@pytest.mark.asyncio
async def test_invoke_stream_with_provider():
    svc = LLMService()
    svc._models["qwen"] = _make_fake_model("QWEN-STREAM")
    chunks = [c async for c in svc.invoke_stream([HumanMessage(content="x")], provider="qwen")]
    assert "QWEN-STREAM" in "".join(chunks)


# ----------------------------------------------------------------------
# Startup with empty providers (graceful)
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_startup_with_no_providers_is_graceful():
    """不配 provider 启动不应报错(只 warning)。"""
    from app.core.config import settings

    settings.llm.providers = {}
    svc = LLMService()
    await svc.startup()
    assert svc.providers == []
    await svc.shutdown()
