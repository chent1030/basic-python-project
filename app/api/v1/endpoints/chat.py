"""LLM endpoints — LangChain + 多 provider(NewAPI 兼容端点)。

所有端点都支持 provider 参数切换供应商;不传则用默认 provider。
- GET  /llm/providers         列出所有可用 provider
- POST /llm/invoke            raw messages(非流式)
- GET  /llm/stream            SSE 流式
- POST /llm/prompt/{name}     按 prompt 文件调用
- POST /llm/pipeline          多步骤 LCEL 编排
"""
from __future__ import annotations

from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.core.auth import AuthUser, require_auth
from app.services.llm import llm

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/providers")
async def list_providers() -> dict[str, object]:
    """列出所有已配置的 LLM provider。"""
    from app.core.config import settings

    return {
        "default": settings.llm.default_provider,
        "providers": list(settings.llm.providers),
    }


# ----------------------------------------------------------------- invoke
class InvokeIn(BaseModel):
    message: str
    provider: str | None = None       # 不传用默认
    model: str | None = None          # 覆盖 provider 默认 model
    temperature: float | None = None


@router.post("/invoke")
async def invoke(req: InvokeIn) -> dict[str, str]:
    """最简调用:单条 user 消息 → 文本返回(非流式)。"""
    text = await llm.invoke(
        [HumanMessage(content=req.message)],
        provider=req.provider,
        model=req.model,
        temperature=req.temperature,
    )
    return {"content": text}


# ----------------------------------------------------------------- stream
@router.get("/stream", response_class=EventSourceResponse)
@require_auth
async def stream(
    prompt: str,
    current_user: AuthUser,  # noqa: ARG001 — proves @require_auth works
    provider: str | None = None,
) -> AsyncIterable[ServerSentEvent]:
    """SSE 流式(需要 Token)。可通过 ?provider=qwen 切换。"""
    async for chunk in llm.invoke_stream(
        [HumanMessage(content=prompt)], provider=provider
    ):
        yield ServerSentEvent(data=chunk, event="delta")
    yield ServerSentEvent(data="[DONE]", event="done")


# ------------------------------------------------------- prompt-driven
class PromptIn(BaseModel):
    variables: dict[str, str] = {}
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None


@router.post("/prompt/{name}")
async def invoke_prompt(name: str, body: PromptIn) -> dict[str, str]:
    """按 prompt 文件名调用:加载 → 渲染 → LLM。"""
    text = await llm.complete_prompt(
        name,
        provider=body.provider,
        overrides={"model": body.model, "temperature": body.temperature},
        **body.variables,
    )
    return {"name": name, "content": text}


@router.post("/prompt/{name}/stream", response_class=EventSourceResponse)
async def stream_prompt(
    name: str, body: PromptIn
) -> AsyncIterable[ServerSentEvent]:
    """按 prompt 文件名流式调用。"""
    async for chunk in llm.complete_prompt_stream(
        name,
        provider=body.provider,
        overrides={"model": body.model, "temperature": body.temperature},
        **body.variables,
    ):
        yield ServerSentEvent(data=chunk, event="delta")
    yield ServerSentEvent(data="[DONE]", event="done")


# ------------------------------------------------------- multi-step LCEL
class PipelineIn(BaseModel):
    """多步骤编排示例:文本 → summarize → translate。"""

    text: str
    target_lang: str = "English"
    max_points: int = 3
    provider: str | None = None       # 两步用同一个 provider


@router.post("/pipeline")
async def pipeline(req: PipelineIn) -> dict[str, str]:
    """LCEL 管道:summarize → translate。

    两步可共用 provider;output_key='source' 与 translate.yaml 变量名对齐。
    """
    pipe = llm.chain(
        "summarize", provider=req.provider, output_key="source"
    ) | llm.chain("translate", provider=req.provider)
    result = await pipe.ainvoke(
        {
            "text": req.text,
            "max_points": req.max_points,
            "lang": "中文",
            "target_lang": req.target_lang,
        }
    )
    return {"pipeline_result": result}
