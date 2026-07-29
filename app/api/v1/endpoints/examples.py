"""Examples — HTTP client utility + prompt-driven LLM calls.

Demonstrates the two utilities:
- app.services.http_client.http_client  — outbound HTTP via httpx
- app.core.prompt + llm.complete_prompt   — prompt files + LangChain LLM

These endpoints are safe to delete once you've seen how the utilities work.
"""
from __future__ import annotations

from collections.abc import AsyncIterable

from fastapi import APIRouter, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from app.core.auth import AuthUser, require_auth
from app.core.prompt import list_prompts, load_prompt, render_prompt
from app.services.http_client import http_client
from app.services.llm import llm

router = APIRouter(prefix="/examples", tags=["examples"])


# ============================================================
# HTTP 客户端示例
# ============================================================
class HttpBinResponse(BaseModel):
    """Generic response from httpbin.org (we only surface a few fields)."""

    url: str
    args: dict[str, str] | None = None
    body: dict | list | None = None
    headers: dict[str, str] | None = None


@router.get("/http/get", response_model=HttpBinResponse)
async def http_get_demo(
    q: str = Query(default="hello", description="任意查询参数,转发给 httpbin"),
) -> HttpBinResponse:
    """演示 `http_client.get` —— 调用外部 GET API。

    调用链:
        await http_client.get(url, params=...) -> HttpResponse
        resp.raise_for_status() / resp.json()
    """
    resp = await http_client.get(
        "https://httpbin.org/anything",
        params={"q": q},
        timeout=10.0,
    )
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f"upstream {resp.status_code}")
    data = resp.json()
    return HttpBinResponse(
        url=data.get("url", ""),
        args=data.get("args"),
        body=data.get("json"),
        headers=data.get("headers"),
    )


@router.post("/http/post", response_model=HttpBinResponse)
async def http_post_demo(payload: dict) -> HttpBinResponse:
    """演示 `http_client.post` —— 发送 JSON body 给外部 API。"""
    resp = await http_client.post(
        "https://httpbin.org/anything",
        json=payload,
        headers={"X-Demo-Header": "fastapi-demo"},
    )
    resp.raise_for_status()
    data = resp.json()
    return HttpBinResponse(
        url=data.get("url", ""),
        body=data.get("json"),
        headers=data.get("headers"),
    )


@router.get("/http/put")
async def http_put_demo() -> dict[str, object]:
    """演示 `http_client.put` —— 发送 PUT 请求(同样支持 patch/delete)。"""
    resp = await http_client.put(
        "https://httpbin.org/anything",
        json={"updated": True},
    )
    return {
        "status": resp.status_code,
        "method": resp.json().get("method"),
        "body": resp.json().get("json"),
    }


# ============================================================
# Prompt 系统示例
# ============================================================
@router.get("/prompts")
async def prompts_index() -> dict[str, list[str]]:
    """列出所有可用 prompt。"""
    return {"prompts": list_prompts()}


@router.get("/prompts/{name}")
async def prompt_detail(name: str) -> dict[str, object]:
    """查看某个 prompt 的结构(不渲染变量)。"""
    try:
        tpl = load_prompt(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "name": tpl.name,
        "system": tpl._raw_system,  # noqa: SLF001 — expose raw template
        "user": tpl._raw_user,
        "variables": [
            {"name": v.name, "required": v.required, "default": v.default,
             "description": v.description}
            for v in tpl.variables
        ],
        "model": tpl.model,
        "temperature": tpl.temperature,
        "max_tokens": tpl.max_tokens,
    }


class PromptRenderIn(BaseModel):
    """Render a prompt with the given variables (no LLM call)."""

    variables: dict[str, str] = {}


@router.post("/prompts/{name}/render")
async def prompt_render(name: str, body: PromptRenderIn) -> dict[str, object]:
    """渲染 prompt(填入变量),但不调用 LLM —— 调试预览用。"""
    try:
        rendered = render_prompt(name, **body.variables)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "name": rendered.name,
        "system": rendered.system,
        "user": rendered.user,
        "messages": rendered.to_messages(),
    }


# ============================================================
# 用 Prompt 调用 LLM
# ============================================================
class TranslateIn(BaseModel):
    source: str
    target_lang: str = "中文"


@router.post("/llm/translate")
async def llm_translate(body: TranslateIn) -> dict[str, str]:
    """用 prompts/translate.yaml + LLM 翻译文本。

    一行代码完成「加载 prompt -> 渲染变量 -> 调用 LLM」。
    """
    text = await llm.complete_prompt(
        "translate",
        source=body.source,
        target_lang=body.target_lang,
    )
    return {"translation": text}


@router.get("/llm/joke/stream", response_class=EventSourceResponse)
@require_auth
async def llm_joke_stream(
    current_user: AuthUser,  # noqa: ARG001 — proves @require_auth works
    topic: str = Query("程序员"),
) -> AsyncIterable[ServerSentEvent]:
    """流式笑话:用 prompts/joke.txt + SSE 输出。需要 Token。"""
    async for chunk in llm.complete_prompt_stream("joke", topic=topic):
        yield ServerSentEvent(data=chunk, event="delta")
    yield ServerSentEvent(data="[DONE]", event="done")
