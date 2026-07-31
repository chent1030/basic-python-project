"""AgentGateway —— 统一入口:trigger / chat + 运行流程编排。

职责:
- 启动时加载 registry(发现所有 agent)+ 全局工具 + 构建 single runner 缓存
- trigger():一次性触发(无状态),source 可为 api/scheduler/internal
- chat():持续对话(有 session_id),走会话记忆中间件
- run():核心流程 —— 中间件洋葱(before/after)+ 后端/runner 编排 + 树状运行记录 + 结构化日志

gateway 复用 app.services.llm 的 provider(单一真相源),不重建 provider 管理。
"""
from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AgentResult, AgentRunContext, Message, TriggerSource
from app.ai.config import AgentConfig
from app.ai.registry import registry
from app.ai.runs import run_store
from app.ai.tools import discover_global_tools
from app.core.logging_config import get_logger

log = get_logger("app.ai.gateway")


class AgentGateway:
    """AI agent 统一网关。单例,挂进 app lifespan。"""

    def __init__(self) -> None:
        self._runners: dict[str, Any] = {}  # agent name -> runner(single 拓扑缓存)
        self._mw_factory: Any = None  # 中间件 pipeline(惰性)
        # HITL:pending 的待确认调用 run_id -> (name, message, ctx, cfg, source, session_id)
        # 第一期内存存储(重启丢失);生产可换 DB
        self._pending: dict[str, dict[str, Any]] = {}

    # ---------- lifecycle ---------------------------------------------
    async def startup(self) -> None:
        """加载全局工具 + registry。确保 LLM provider 已就绪。

        幂等:若 llm 已 startup(app lifespan 先调过)则不重复;否则在此补启,
        保证独立脚本/demo 里只调 agent_gateway.startup() 也能用。
        """
        # 确保 LLM provider 已构建(gateway 强依赖它取模型)
        from app.services.llm import llm as llm_svc

        if not llm_svc.providers:
            await llm_svc.startup()

        # 确保数据源就绪(agent_runs / agent_sessions 写库依赖;连不上则记录功能降级)
        from app.core.datasource import datasources

        if not datasources.names():
            await datasources.startup()

        discover_global_tools()
        registry.load()
        self._mw_factory = _build_middleware_factory()
        log.info(
            "agent_gateway 就绪: %d 个 agent %s",
            len(registry.names()), registry.names() or "(无)",
        )

    async def shutdown(self) -> None:
        self._runners.clear()
        self._mw_factory = None

    # ---------- 入口:一次性触发 ---------------------------------------
    async def trigger(
        self,
        name: str,
        message: str,
        *,
        source: TriggerSource = "api",
        session_id: str | None = None,
        user_id: str | None = None,
        **vars: Any,
    ) -> AgentResult:
        """一次性触发一个 agent。无状态(除非挂了持久/外部记忆中间件)。

        source: 触发来源(api/scheduler/internal),写入运行记录便于区分。
        """
        cfg = registry.get(name)
        if cfg.mode != "trigger" and session_id is None:
            # 允许对 chat 模式的 agent 也做一次性触发,但不推荐
            log.debug("agent '%s' 配置为 chat 模式,但以 trigger 方式调用", name)
        messages = self._build_messages(cfg, message, **vars)
        ctx = AgentRunContext(
            agent_name=name,
            messages=messages,
            session_id=session_id,
            source=source,
            user_id=user_id,
        )
        # HITL:需要人确认的 agent,第一次调用返回 pending(不执行)
        pending = self._check_hitl(name, message, ctx, cfg, source, session_id)
        if pending is not None:
            return pending
        return await self._run(ctx, cfg)

    async def trigger_stream(
        self, name: str, message: str, *, source: TriggerSource = "api", **vars: Any
    ) -> AsyncIterator[str]:
        """流式触发(实时 yield 输出文本片段)。"""
        cfg = registry.get(name)
        messages = self._build_messages(cfg, message, **vars)
        ctx = AgentRunContext(agent_name=name, messages=messages, source=source)
        async for chunk in self._run_stream(ctx, cfg):
            yield chunk

    # ---------- 入口:持续对话 -----------------------------------------
    async def chat(
        self,
        name: str,
        session_id: str,
        message: str,
        *,
        source: TriggerSource = "api",
        user_id: str | None = None,
    ) -> AgentResult:
        """持续对话:按 session_id 维护历史(会话记忆中间件)。

        本轮 user 消息先不拼进 messages —— 交给 session_memory 中间件在 before 阶段
        load 历史 + 本轮消息一起拼。这样历史拼接逻辑集中在中间件里。
        """
        cfg = registry.get(name)
        # 只放本轮 user 消息;session_memory 中间件会把历史 prepend 上
        messages: list[Message] = [{"role": "user", "content": message}]
        ctx = AgentRunContext(
            agent_name=name,
            messages=messages,
            session_id=session_id,
            source=source,
            user_id=user_id,
        )
        # HITL:需要人确认的 agent,第一次调用返回 pending(不执行)
        pending = self._check_hitl(name, message, ctx, cfg, source, session_id)
        if pending is not None:
            return pending
        return await self._run(ctx, cfg)

    async def chat_stream(
        self, name: str, session_id: str, message: str, *, source: TriggerSource = "api"
    ) -> AsyncIterator[str]:
        cfg = registry.get(name)
        messages: list[Message] = [{"role": "user", "content": message}]
        ctx = AgentRunContext(
            agent_name=name, messages=messages, session_id=session_id, source=source
        )
        async for chunk in self._run_stream(ctx, cfg):
            yield chunk

    # ---------- HITL:Human-in-the-loop 暂停/恢复 ----------------------
    def _check_hitl(
        self, name: str, message: str, ctx: AgentRunContext, cfg: AgentConfig,
        source: TriggerSource, session_id: str | None,
    ) -> AgentResult | None:
        """若 agent 配置了 hitl.require_confirmation,挂起本次调用,返回 pending。

        把调用上下文存入 self._pending,等 confirm(run_id) 批准后取出继续执行。
        返回 None=不需要确认,正常执行;返回 AgentResult=pending 状态。
        """
        if not cfg.hitl.require_confirmation:
            return None
        run_id = ctx.extra.get("run_id") or uuid.uuid4().hex
        self._pending[run_id] = {
            "name": name, "message": message, "ctx": ctx, "cfg": cfg,
            "source": source, "session_id": session_id,
        }
        log.info("HITL:agent '%s' 待确认 run_id=%s(批准后执行)", name, run_id)
        return AgentResult(
            output="",
            extra={
                "status": "awaiting_confirmation",
                "run_id": run_id,
                "agent": name,
                "message": (
                    f"本次调用需人工确认。"
                    f"POST /agents/{name}/runs/{run_id}/confirm 批准后执行"
                ),
            },
        )

    async def confirm(self, run_id: str, decision: str = "approve") -> AgentResult:
        """批准/拒绝一个挂起的 HITL 调用。

        decision: approve(执行) | reject(拒绝,返回取消信息)
        批准后取出挂起的上下文,继续走完整 _run 流程。
        """
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return AgentResult(
                output="",
                extra={
                    "status": "not_found",
                    "run_id": run_id,
                    "error": "无此待确认调用(可能已过期或已处理)",
                },
            )
        if decision == "reject":
            log.info("HITL:run_id=%s 已拒绝", run_id)
            return AgentResult(
                output="",
                extra={"status": "rejected", "run_id": run_id, "agent": pending["name"]},
            )
        # approve:取出上下文继续执行
        log.info("HITL:run_id=%s 已批准,开始执行 agent '%s'", run_id, pending["name"])
        return await self._run(pending["ctx"], pending["cfg"])

    # ---------- 核心:运行流程 -----------------------------------------
    async def _run(
        self,
        ctx: AgentRunContext,
        cfg: AgentConfig,
        *,
        parent_run_id: str | None = None,
        depth: int = 0,
    ) -> AgentResult:
        """单次运行的完整流程:中间件 + 编排 + 记录 + 日志。

        多拓扑成员 agent 也走本方法(带 parent_run_id/depth),形成树状调用记录。
        """
        started = time.monotonic()
        ctx.parent_run_id = parent_run_id
        ctx.depth = depth

        run_id = await run_store.create(
            agent_name=ctx.agent_name,
            trigger_source=ctx.source,
            input_text=ctx.last_user_message,
            session_id=ctx.session_id,
            parent_run_id=parent_run_id,
            depth=depth,
        )
        ctx.extra["run_id"] = run_id
        ctx.logger.info(
            "agent 开始 name=%s source=%s depth=%d run_id=%s",
            ctx.agent_name, ctx.source, depth, run_id,
        )

        result = AgentResult()
        try:
            # 1) before 中间件(顺序):session/persistent/external 读、filter 输入、context 透传
            ctx = await self._ensure_mw().before(ctx, cfg)

            # 2) 编排:按拓扑选 runner,成员 agent 通过 run_member 回调递归进 _run
            runner = self._get_runner(cfg)
            result = await runner.run(ctx, self._run_member)

            # 3) after 中间件(逆序):filter 输出、persistent/session 写、tracing 记录
            result = await self._ensure_mw().after(ctx, cfg, result)

            duration = int((time.monotonic() - started) * 1000)
            await run_store.mark_succeeded(
                run_id, output=result.output, tokens=result.tokens
            )
            await run_store.set_duration(run_id, duration)
            ctx.logger.info(
                "agent 完成 name=%s run_id=%s duration=%dms tokens=%s",
                ctx.agent_name, run_id, duration, result.tokens,
            )
            return result
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            await run_store.mark_failed(run_id, error=str(exc))
            await run_store.set_duration(run_id, duration)
            ctx.logger.exception(
                "agent 失败 name=%s run_id=%s duration=%dms",
                ctx.agent_name, run_id, duration,
            )
            raise

    async def _run_member(
        self,
        name: str,
        message: str,
        parent_ctx: AgentRunContext,
    ) -> AgentResult:
        """成员 agent 执行回调(runner 用它跑成员)。

        成员 agent 走完整 _run 流程,parent_run_id/depth 取自父上下文 → 树状记录。
        成员以 trigger 方式跑(不复用父的 session,避免历史串扰)。
        """
        cfg = registry.get(name)
        messages = self._build_messages(cfg, message)
        child = AgentRunContext(
            agent_name=name,
            messages=messages,
            source="internal",
            parent_run_id=parent_ctx.extra.get("run_id"),
            depth=parent_ctx.depth + 1,
            user_id=parent_ctx.user_id,
        )
        return await self._run(
            child, cfg,
            parent_run_id=parent_ctx.extra.get("run_id"),
            depth=parent_ctx.depth + 1,
        )

    async def _run_stream(self, ctx: AgentRunContext, cfg: AgentConfig) -> AsyncIterator[str]:
        """流式运行:只跑 single 拓扑(多拓扑流式较复杂,第一期 single-only)。"""
        from app.ai.runners.single import SingleRunner

        runner = self._get_runner(cfg)
        if not isinstance(runner, SingleRunner):
            # 非单 agent 拓扑:降级为非流式,把完整输出一次性 yield
            result = await self._run(ctx, cfg)
            yield result.output
            return

        ctx.extra["run_id"] = "stream-(not-recorded)"
        ctx = await self._ensure_mw().before(ctx, cfg)
        chunks: list[str] = []
        async for chunk in runner._backend.stream(ctx):
            chunks.append(chunk)
            yield chunk
        result = AgentResult(output="".join(chunks))
        result = await self._ensure_mw().after(ctx, cfg, result)

    # ---------- helpers -----------------------------------------------
    def _ensure_mw(self):
        """惰性构建中间件 pipeline。startup 未调或被测试重置时也能用。"""
        if self._mw_factory is None:
            self._mw_factory = _build_middleware_factory()
        return self._mw_factory

    def _get_runner(self, cfg: AgentConfig):
        """按拓扑选 runner。single 按 agent 目录缓存(配置相同可复用);复合拓扑每次新建。"""
        from app.ai.runners import pick_runner

        if cfg.topology == "single":
            # single runner 绑定了该 agent 的 cfg(provider/tools/prompt),按目录路径缓存复用
            key = cfg.dir or cfg.backend
            if key not in self._runners:
                self._runners[key] = pick_runner(cfg.topology)(cfg)
            return self._runners[key]
        # 复合拓扑:每次新建 runner(成员配置各异,且 runner 内部要解析成员)
        return pick_runner(cfg.topology)(cfg)

    def _build_messages(self, cfg: AgentConfig, message: str, **vars: Any) -> list[Message]:
        """构造初始消息:system_prompt + user 消息。

        若 config 指定 prompt_file,从 prompts/ 加载(复用 app.core.prompt)。
        """
        msgs: list[Message] = []
        if cfg.system_prompt:
            msgs.append({"role": "system", "content": cfg.system_prompt})
        elif cfg.prompt_file:
            # 复用现有 prompt 系统(render_prompt 返回 system/user)
            try:
                from app.core.prompt import render_prompt

                rendered = render_prompt(cfg.prompt_file, **vars)
                if rendered.system:
                    msgs.append({"role": "system", "content": rendered.system})
                user = rendered.user or message
                msgs.append({"role": "user", "content": user})
                return msgs
            except Exception:
                log.exception("加载 prompt_file '%s' 失败,降级用裸消息", cfg.prompt_file)
        msgs.append({"role": "user", "content": message})
        return msgs

    # ---------- 查询(给 API 用)---------------------------------------
    def list_agents(self) -> list[dict[str, Any]]:
        return [registry.info(n) for n in registry.names()]

    def get_agent_info(self, name: str) -> dict[str, Any]:
        return registry.info(name)


# --------------------------------------------------------------------------
# 中间件 pipeline 工厂:惰性构建已注册的中间件,按 agent config 选取挂载。
# (中间件实现在 app/ai/middleware/,本函数在 gateway 启动时调用)
# --------------------------------------------------------------------------
def _build_middleware_factory():
    from app.ai.middleware import build_factory

    return build_factory()


# 单例 —— 由 app lifespan 启停
agent_gateway = AgentGateway()


__all__ = ["AgentGateway", "agent_gateway"]
