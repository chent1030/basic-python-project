"""Tests for the AI agent framework.

Covers:
- AgentConfig parsing from yaml
- registry discovery (real app/ai/agents dir)
- tool registration (global + exclusive)
- gateway run flow with a STUB backend (no real LLM call): single/parallel/
  sequential/conversational/router via run_member
- tree-shaped agent_runs recording (parent_run_id / depth)
- middleware onion order + 4 memory types
- session/run stores via in-memory SQLite

We stub the single backend (monkeypatch build_backend) so the whole run
pipeline executes without hitting any LLM API — mirrors how test_llm_langchain
uses FakeListChatModel.
"""
from __future__ import annotations

import asyncio
import tempfile
import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  register all ORM models
from app.ai.base import AgentResult, AgentRunContext
from app.ai.config import AgentConfig
from app.db.base import Base


# ----------------------------------------------------------------------
# In-memory SQLite engine + table creation (module-scoped)
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:")


@pytest.fixture(scope="module")
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module")
def _create_tables(engine):
    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())


@pytest.fixture(autouse=True)
def _wire_datasource(session_factory, _create_tables):  # noqa: ARG001
    """把内存 SQLite 注入 datasources,供 stores 使用。每个测试函数前重置。"""
    from app.core.datasource import datasources

    datasources._session_factories["postgres_primary"] = session_factory
    datasources._types["postgres_primary"] = "postgresql"
    yield
    # 不清掉(datasources 是全局单例),下个测试会覆盖同 key


# ----------------------------------------------------------------------
# Stub backend: returns canned text, no real LLM
# ----------------------------------------------------------------------
class _StubBackend:
    backend_name = "stub"

    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg

    async def invoke(self, ctx: AgentRunContext) -> AgentResult:
        return AgentResult(output=f"[{self.cfg.system_prompt}] -> {ctx.last_user_message}")

    async def stream(self, ctx: AgentRunContext):  # type: ignore[override]
        yield f"[{self.cfg.system_prompt}]"


@pytest.fixture(autouse=True)
def _stub_single_backend(monkeypatch):
    """替换 single runner 的后端构建,避免真实 LLM 调用。"""
    import app.ai.runners.single as single_mod

    monkeypatch.setattr(single_mod, "build_backend", lambda cfg: _StubBackend(cfg))
    yield


# ----------------------------------------------------------------------
# Temp agents dir fixture: writes a few agents for topology tests
# ----------------------------------------------------------------------
@pytest.fixture
def temp_agents(monkeypatch):
    d = tempfile.mkdtemp(prefix="ai_agents_")
    def _w(name, yml):
        import os

        p = os.path.join(d, name)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "config.yml"), "w") as f:
            f.write(textwrap.dedent(yml))

    _w("alpha", """\
        topology: single
        backend: deepagents
        system_prompt: A
        middleware:
          - name: tracing
          - name: session_memory
    """)
    _w("beta", """\
        topology: single
        backend: agentscope
        system_prompt: B
        middleware:
          - name: tracing
    """)
    _w("par", "topology: parallel\nmembers: [alpha, beta]\naggregator: merge\n")
    _w("seq", "topology: sequential\nmembers: [alpha, beta]\n")
    _w("conv", "topology: conversational\nmembers: [alpha, beta]\nrounds: 2\n")
    _w("rtr", "topology: router\nroutes:\n  x: alpha\n  y: beta\n  _default: beta\n")
    # pipeline: alpha → [alpha, beta 并行] → beta(顺序中嵌入并行)
    _w("pipe", textwrap.dedent("""\
        topology: pipeline
        steps:
          - name: s1
            run: alpha
          - name: s2
            parallel: [alpha, beta]
            aggregator: merge
          - name: s3
            run: beta
    """))

    from app.core.config import settings

    monkeypatch.setattr(settings.agents, "agents_dir", d)

    # rebuild registry against the temp dir
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    monkeypatch.setattr("app.ai.gateway.registry", reg)
    monkeypatch.setattr("app.ai.runners.router.registry_obj", reg, raising=False)
    # router uses module-level registry import; patch it
    import app.ai.runners.router as router_mod

    monkeypatch.setattr(router_mod, "registry", reg, raising=False)
    return reg


# ======================================================================
# AgentConfig parsing
# ======================================================================
def test_agentconfig_from_yaml():
    import pathlib

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(textwrap.dedent("""
            topology: subagent
            backend: deepagents
            subagents: [a, b]
            middleware:
              - name: tracing
              - name: memory
                config: {top_k: 7}
        """))
        path = pathlib.Path(f.name)
    cfg = AgentConfig.from_yaml_file(path)
    assert cfg.topology == "subagent"
    assert cfg.subagents == ["a", "b"]
    assert [m.name for m in cfg.middleware] == ["tracing", "memory"]
    assert cfg.middleware[1].config == {"top_k": 7}


def test_agentconfig_defaults():
    cfg = AgentConfig()
    assert cfg.topology == "single"
    assert cfg.backend == "deepagents"
    assert cfg.mode == "trigger"
    assert cfg.recursion_limit == 25


# ======================================================================
# Registry: real app/ai/agents dir loads all topologies
# ======================================================================
def test_registry_loads_real_agents():
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    names = set(reg.names())
    # 至少覆盖 6 种拓扑的示例
    assert "researcher" in names          # single
    assert "research_team" in names       # subagent
    assert "content_pipeline" in names    # sequential
    assert "review_squad" in names        # parallel
    assert "debate_room" in names         # conversational
    assert "dispatcher" in names          # router


def test_registry_has_both_backend_pairs():
    """每个场景都有 deepagents(_da)和 agentscope(_as)成对实现。"""
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    names = set(reg.names())
    # 成对覆盖(两后端各一份)
    for da, as_ in [
        ("researcher_da", "researcher_as"),          # single trigger
        ("support_bot_da", "support_bot_as"),        # chat
        ("tool_demo_da", "tool_demo_as"),            # tool
        ("middleware_demo_da", "middleware_demo_as"),  # middleware
        ("review_squad_da", "review_squad_as"),      # parallel
        ("content_pipeline_da", "content_pipeline"),  # sequential
        ("debate_room_da", "debate_room"),           # conversational
        ("research_team_da", "research_team_as"),    # subagent vs seq-equiv
    ]:
        assert da in names, f"缺少 deepagents 版 agent: {da}"
        assert as_ in names, f"缺少 agentscope 版 agent: {as_}"


def test_registry_get_unknown_raises():
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    with pytest.raises(KeyError, match="未注册"):
        reg.get("does_not_exist")


def test_registry_resolves_members():
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    cfg = reg.get("content_pipeline")
    assert cfg.members == ["summarizer", "translator", "proofreader"]
    # members 引用的 agent 都应可解析
    for m in cfg.members:
        assert reg.has(m)


# ======================================================================
# Tool system
# ======================================================================
def test_tool_registration_and_resolve():
    from app.ai.tools import all_tools, clear_tools, resolve_tools, tool

    clear_tools()

    @tool("t1")
    async def t1(x: str) -> str:
        """tool one."""

        return x

    assert "t1" in all_tools()
    resolved = resolve_tools(["t1", "missing"])
    assert len(resolved) == 1 and resolved[0].name == "t1"
    assert resolved[0].is_async is True
    assert resolved[0].description == "tool one."


# ======================================================================
# Gateway run flow + topologies (stub backend, no real LLM)
# ======================================================================
@pytest.mark.asyncio
async def test_trigger_single(temp_agents):
    from app.ai.gateway import agent_gateway

    r = await agent_gateway.trigger("alpha", "hello")
    assert "[A]" in r.output and "hello" in r.output
    assert r.extra.get("topology") != "parallel"  # single 没标 topology


@pytest.mark.asyncio
async def test_trigger_parallel_merges_members(temp_agents):
    from app.ai.gateway import agent_gateway

    r = await agent_gateway.trigger("par", "topic")
    assert "[A]" in r.output and "[B]" in r.output  # 两成员输出都被合并
    assert r.extra["topology"] == "parallel"


@pytest.mark.asyncio
async def test_trigger_sequential_chains(temp_agents):
    from app.ai.gateway import agent_gateway

    r = await agent_gateway.trigger("seq", "start")
    # beta 的输入应是 alpha 的输出 → beta 输出里含 alpha 的标记
    assert "[B]" in r.output
    assert "[A]" in r.output  # 链式:beta 回显了 alpha 的输出
    assert r.extra["topology"] == "sequential"


@pytest.mark.asyncio
async def test_trigger_pipeline_mixed(temp_agents):
    """pipeline:顺序步骤 + 中间并行 + 合并 + 后续步骤。
    流程 alpha → [alpha,beta 并行] → beta。
    验证:并行步骤等待全部完成、合并后喂给后续、步骤记录正确。
    """
    from app.ai.gateway import agent_gateway

    r = await agent_gateway.trigger("pipe", "topic")
    # 最终输出来自最后一步 beta
    assert "[B]" in r.output
    assert r.extra["topology"] == "pipeline"
    steps = r.extra["steps"]
    # 3 个步骤:s1(single) s2(parallel) s3(single)
    assert len(steps) == 3
    assert steps[0]["kind"] == "single"
    assert steps[1]["kind"] == "parallel"
    assert steps[1]["members"] == ["alpha", "beta"]
    # 并行步骤合并后含两成员输出(merge 拼接)
    merged = r.extra["step_outputs"]["s2"]
    assert "[A]" in merged and "[B]" in merged
    # 并行成员都跑完(各 1 条输出)
    assert len(steps[1]["outputs"]) == 2


@pytest.mark.asyncio
async def test_pipeline_custom_aggregator(temp_agents):
    """自定义聚合器:工程师自己写合并逻辑,@aggregator 注册后 config 引用。"""
    from app.ai.aggregators import aggregator, clear_aggregators
    from app.ai.gateway import agent_gateway

    # 定义自定义聚合器(模拟 agent 目录里 aggregator.py 的写法)
    @aggregator("test_custom")
    def _custom(outputs):
        return "CUSTOM:" + ";".join(f"{n}={o[:3]}" for n, o in outputs)

    # 临时建一个用自定义聚合器的 pipeline
    import os

    from app.core.config import settings

    pipe_dir = os.path.join(settings.agents.agents_dir, "pipe2")
    os.makedirs(pipe_dir, exist_ok=True)
    with open(os.path.join(pipe_dir, "config.yml"), "w") as f:
        f.write(textwrap.dedent("""\
            topology: pipeline
            steps:
              - name: p
                parallel: [alpha, beta]
                aggregator: test_custom
        """))
    # 重新 load registry(让它发现 pipe2)
    from app.ai.registry import AgentRegistry

    reg = AgentRegistry()
    reg.load()
    import app.ai.gateway as gw

    gw.registry = reg

    r = await agent_gateway.trigger("pipe2", "x")
    # 自定义聚合器输出格式:CUSTOM:alpha=[A;beta=[B
    assert r.output.startswith("CUSTOM:")
    assert "alpha=" in r.output and "beta=" in r.output
    clear_aggregators()


@pytest.mark.asyncio
async def test_trigger_conversational(temp_agents):
    from app.ai.gateway import agent_gateway

    r = await agent_gateway.trigger("conv", "issue")
    assert r.output  # 有输出
    assert r.extra["topology"] == "conversational"
    assert len(r.extra["transcript"]) >= 2  # 至少两轮发言


@pytest.mark.asyncio
async def test_trigger_router(temp_agents, monkeypatch):
    from app.ai.gateway import agent_gateway

    # 桩掉 LLM 分类:router._classify 用 llm.invoke,这里直接返回固定意图
    async def _fake_classify(self, message):
        return "x"

    import app.ai.runners.router as router_mod

    monkeypatch.setattr(router_mod.RouterRunner, "_classify", _fake_classify)
    r = await agent_gateway.trigger("rtr", "whatever")
    assert r.extra["topology"] == "router"
    assert r.extra["router"]["target"] == "alpha"  # 意图 x -> alpha


# ======================================================================
# Tree-shaped run recording (parent_run_id / depth)
# ======================================================================
@pytest.mark.asyncio
async def test_run_tree_shape(temp_agents):
    from app.ai.gateway import agent_gateway
    from app.ai.runs import run_store

    await agent_gateway.trigger("par", "topic")
    runs = await run_store.list_runs("par", limit=5)
    root = runs[0]
    # 取完整调用树
    tree = await run_store.get_tree(root["run_id"])
    assert tree["root"]["agent_name"] == "par"
    assert tree["root"]["depth"] == 0
    # 两个成员子节点
    children = tree["tree"]
    assert len(children) == 2
    assert all(c["depth"] == 1 for c in children)
    assert {c["agent_name"] for c in children} == {"alpha", "beta"}
    assert all(c["parent_run_id"] == root["run_id"] for c in children)


@pytest.mark.asyncio
async def test_run_records_status_and_duration(temp_agents):
    from app.ai.gateway import agent_gateway
    from app.ai.runs import run_store

    await agent_gateway.trigger("alpha", "hi")
    runs = await run_store.list_runs("alpha", limit=1)
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["duration_ms"] is not None
    assert runs[0]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_run_records_failure(temp_agents, monkeypatch):
    from app.ai.gateway import agent_gateway
    from app.ai.runs import run_store

    # 让 stub 抛错
    class _Boom(_StubBackend):
        async def invoke(self, ctx):
            raise RuntimeError("boom")

    import app.ai.runners.single as single_mod

    monkeypatch.setattr(single_mod, "build_backend", lambda cfg: _Boom(cfg))
    with pytest.raises(RuntimeError, match="boom"):
        await agent_gateway.trigger("alpha", "x")
    runs = await run_store.list_runs("alpha", limit=1)
    assert runs[0]["status"] == "failed"
    assert "boom" in (runs[0]["error"] or "")


# ======================================================================
# Middleware: onion order + memory types
# ======================================================================
@pytest.mark.asyncio
async def test_middleware_onion_order(temp_agents, monkeypatch):
    """验证 before 顺序、after 逆序(洋葱模型)。"""
    from app.ai.config import MiddlewareSpec
    from app.ai.middleware import MiddlewarePipeline
    from app.ai.middleware.base import MiddlewareBase

    order: list[str] = []

    class _M(MiddlewareBase):
        def __init__(self, tag):
            self.tag = tag

        async def before_invoke(self, ctx, cfg):
            order.append(f"before-{self.tag}")
            return ctx

        async def after_invoke(self, ctx, cfg, result):
            order.append(f"after-{self.tag}")
            return result

    pipe = MiddlewarePipeline()
    pipe._instances = {f"m{i}": _M(t) for i, t in enumerate(["a", "b", "c"])}
    specs = [MiddlewareSpec(name=n) for n in ("ma", "mb", "mc")]
    cfg = AgentConfig(middleware=specs)
    # 让 _get 直接返回我们的实例
    pipe._instances = {"ma": _M("a"), "mb": _M("b"), "mc": _M("c")}

    ctx = AgentRunContext(agent_name="x", messages=[{"role": "user", "content": "hi"}])
    await pipe.before(ctx, cfg)
    await pipe.after(ctx, cfg, AgentResult(output="o"))
    assert order == ["before-a", "before-b", "before-c", "after-c", "after-b", "after-a"]


@pytest.mark.asyncio
async def test_session_memory_loads_and_appends(temp_agents):
    """chat 模式:会话记忆中间件应 load 历史 + append 本轮。"""
    from app.ai.gateway import agent_gateway
    from app.ai.session import session_store

    # 先存一段历史
    await session_store.append_turn("alpha", "sess-1", "old q", "old a")
    r = await agent_gateway.chat("alpha", "sess-1", "new q")
    assert "new q" in r.output
    # 本轮 user+assistant 应被追加
    history = await session_store.load_history("alpha", "sess-1")
    roles = [m["role"] for m in history]
    assert roles.count("user") >= 2 and roles.count("assistant") >= 2


# ======================================================================
# Stores: run + session via in-memory SQLite
# ======================================================================
@pytest.mark.asyncio
async def test_session_store_roundtrip():
    from app.ai.session import session_store

    await session_store.append_message("ag", "s1", "user", "hello")
    await session_store.append_message("ag", "s1", "assistant", "hi there")
    hist = await session_store.load_history("ag", "s1")
    assert len(hist) == 2
    assert hist[0] == {"role": "user", "content": "hello"}
    assert hist[1] == {"role": "assistant", "content": "hi there"}


@pytest.mark.asyncio
async def test_memory_store_disabled_is_noop():
    """persistent_memory 默认 disabled → recall/remember 是 no-op。"""
    from app.ai.memory import memory_store

    assert await memory_store.recall("ag", "q") == []
    await memory_store.remember("ag", "fact")  # 不应抛错
