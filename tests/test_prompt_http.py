"""Tests for the prompt loader and HTTP client wrapper.

We do NOT hit the network — http_client is exercised with a fake transport,
and the prompt system reads the real files under /prompts.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.prompt import (
    RenderedPrompt,
    list_prompts,
    load_prompt,
    render_prompt,
)
from app.services.http_client import HttpClient


# ============================================================
# Prompt 系统
# ============================================================
def test_list_prompts_includes_shipped_examples():
    names = set(list_prompts())
    assert {"translate", "joke", "summarize"} <= names


def test_load_yaml_prompt_has_declared_variables():
    tpl = load_prompt("translate")
    var_names = {v.name for v in tpl.variables}
    assert var_names == {"source", "target_lang"}
    assert tpl.temperature == 0.3


def test_render_yaml_prompt_substitutes_variables():
    rendered = render_prompt("translate", source="Hello", target_lang="中文")
    assert isinstance(rendered, RenderedPrompt)
    assert "Hello" in rendered.user
    assert "中文" in rendered.system
    assert "中文" in rendered.user
    msgs = rendered.to_messages()
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"


def test_render_missing_required_variable_raises():
    # `source` is required by translate.yaml
    with pytest.raises(ValueError, match="missing required variables"):
        render_prompt("translate", target_lang="中文")  # source omitted


def test_render_uses_declared_default_when_optional_var_omitted():
    # `target_lang` has default 中文; `count` is optional in joke.txt
    rendered = render_prompt("joke", topic="程序员")
    assert "程序员" in (rendered.user or "")


def test_text_prompt_with_front_matter_parsed():
    tpl = load_prompt("joke")
    assert tpl.temperature == 0.9
    var_names = {v.name for v in tpl.variables}
    assert var_names == {"topic", "count"}


def test_jinja_template_prompt_renders():
    rendered = render_prompt("summarize", text="SOME LONG TEXT", max_points=3, lang="English")
    assert "SOME LONG TEXT" in rendered.user
    assert "English" in rendered.user
    # Jinja2 conditional should expand: "不超过 3 条"
    assert "3" in rendered.user


def test_load_unknown_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("does-not-exist")


def test_prompt_cache_returns_same_instance():
    a = load_prompt("translate")
    b = load_prompt("translate")
    assert a is b


# ============================================================
# HTTP 客户端 — 用 httpx.MockTransport 模拟,不联网
# ============================================================
def _make_mock_handler():
    """Captures the request and returns a canned response."""
    received: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["method"] = request.method
        received["url"] = str(request.url)
        received["headers"] = dict(request.headers)
        try:
            received["json"] = request.read() and __import__("json").loads(
                request.read() if False else (request.content or b"null")
            )
        except Exception:
            received["json"] = None
        return httpx.Response(200, json={"ok": True, "echo_method": request.method})

    return handler, received


@pytest.mark.asyncio
async def test_http_client_get_post_put_delete(monkeypatch):
    handler, received = _make_mock_handler()
    transport = httpx.MockTransport(handler)

    # Bypass startup() and inject a client with the mock transport.
    client = HttpClient()
    client._client = httpx.AsyncClient(transport=transport)

    # GET
    resp = await client.get("https://example.com/items", params={"q": "x"})
    assert resp.status_code == 200
    assert resp.json()["echo_method"] == "GET"
    assert "q=x" in received["url"]

    # POST with JSON
    resp = await client.post("https://example.com/items", json={"name": "a"})
    assert resp.ok
    assert received["method"] == "POST"
    assert received["json"] == {"name": "a"}

    # PUT
    resp = await client.put("https://example.com/items/1", json={"name": "b"})
    assert resp.json()["echo_method"] == "PUT"

    # PATCH
    resp = await client.patch("https://example.com/items/1", json={"name": "c"})
    assert resp.json()["echo_method"] == "PATCH"

    # DELETE
    resp = await client.delete("https://example.com/items/1")
    assert resp.json()["echo_method"] == "DELETE"

    await client.shutdown()
    assert client._client is None


def _ok_handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _not_found_handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"detail": "not found"})


@pytest.mark.asyncio
async def test_http_response_raise_for_status_passes_on_2xx():
    client = HttpClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler))
    resp = await client.get("https://example.com/")
    # Should not raise; returns self for chaining.
    assert resp.raise_for_status() is resp
    await client.shutdown()


@pytest.mark.asyncio
async def test_http_response_raise_for_status_raises_on_4xx():
    client = HttpClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_not_found_handler))
    resp = await client.get("https://example.com/")
    with pytest.raises(httpx.HTTPStatusError):
        resp.raise_for_status()
    await client.shutdown()


@pytest.mark.asyncio
async def test_http_client_before_startup_raises():
    client = HttpClient()
    with pytest.raises(RuntimeError, match="before startup"):
        await client.get("https://example.com/")


# ============================================================
# Examples router smoke: list_prompts & render endpoints
# (uses the same TestClient-without-lifespan trick as smoke_test.py)
# ============================================================
def test_examples_endpoint_lists_prompts():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/examples/prompts")
        assert r.status_code == 200
        names = set(r.json()["prompts"])
        assert {"translate", "joke", "summarize"} <= names


def test_examples_render_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/v1/examples/prompts/translate/render",
            json={"variables": {"source": "Hi", "target_lang": "中文"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert "Hi" in body["user"]
        assert body["messages"][0]["role"] == "system"
