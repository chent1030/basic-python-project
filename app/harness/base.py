"""BaseAgent —— 文档审核仍使用的旧单 Agent 兼容基类。

现有业务继承 BaseSingleAgent；新业务使用 app.harness.kernel。
此层提供:声明式配置(类属性) + run() 主流程 + 后端调用 + 中间件 + HITL + 通讯 API。

核心流程:
    run(message) → 中间件 before → _execute_topology() → 中间件 after → 记录
    HITL:require_confirmation=True 时首次返回 pending,confirm 后执行
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

from app.core.logging_config import get_logger
from app.harness.context import AgentResult, AgentRunContext, Message

log = get_logger("app.harness.base")


class BaseAgent:
    """所有 agent 的底层基类。

    子类(拓扑基类)设类属性声明配置,实现 _execute_topology()。
    业务代码继承拓扑基类(如 BaseSingleAgent),设类属性即可使用。
    """

    # ============ 声明式配置(子类设类属性)============
    name: str = ""
    backend: str = "deepagents"
    provider: str = ""  # LLM provider(空=默认)
    system_prompt: str = ""
    prompt_file: str = ""
    tools: list[str] = []  # 工具名(@tool 注册的)
    middleware: list[str] = []  # 中间件名(tracing/filter/session_memory...)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    recursion_limit: int = 25
    skills: list[str] = []
    hitl_require_confirmation: bool = False

    # ============ 运行时状态(框架注入)============
    _context: AgentRunContext | None = None
    _backend_obj: Any = None  # 惰性构建的后端对象

    def _run_context_var(self) -> ContextVar[AgentRunContext | None]:
        """Return this instance's task-local run context."""
        run_context = self.__dict__.get("_task_run_context")
        if run_context is None:
            run_context = ContextVar(f"harness_agent_context_{id(self)}", default=None)
            self.__dict__["_task_run_context"] = run_context
        return run_context

    # ============ 主流程 ============
    async def run(
        self,
        message: str,
        *,
        session_id: str | None = None,
        source: str = "api",
        context: AgentRunContext | None = None,
    ) -> AgentResult:
        """核心执行流程。

        业务代码调 agent.run("...") 触发。
        框架自动处理:中间件洋葱 + HITL + 后端调用 + 运行记录。
        """
        # 构造/复用 context
        if context is not None:
            ctx = context
            # 外部传入 context:若其中尚无 user 消息,补上本次 message 作为 user 轮,
            # 保证深链条编排(共享同一 ctx)时,后端(deepagents)一定能拿到 user 提问,
            # 否则上游可能返回 "No user query found in messages" 400。
            if not any(m.get("role") == "user" for m in ctx.messages):
                ctx.messages.extend(self._build_messages(message))
        else:
            ctx = AgentRunContext(
                agent_name=self.name,
                messages=self._build_messages(message),
                session_id=session_id,
                source=source,
            )
        self._context = ctx
        context_var = self._run_context_var()
        context_token = context_var.set(ctx)
        try:
            ctx.logger.info("agent 开始 name=%s run_id=%s", self.name, ctx.run_id)

            # HITL:需要确认时首次返回 pending
            if self.hitl_require_confirmation:
                return AgentResult(
                    output="",
                    extra={
                        "status": "awaiting_confirmation",
                        "run_id": ctx.run_id,
                        "agent": self.name,
                    },
                )

            started = time.monotonic()
            result = AgentResult()
            try:
                from app.harness.communication.blackboard_var import use_blackboard

                with use_blackboard(ctx.blackboard):
                    # 1. 中间件 before
                    ctx = await self._run_middleware_before(ctx)

                # Middleware may replace the context and its communication facilities.
                self._context = ctx
                current_token = context_var.set(ctx)
                try:
                    with use_blackboard(ctx.blackboard):
                        # 2. 拓扑执行(子类实现)
                        result = await self._execute_topology(ctx)

                        # 3. 中间件 after
                        result = await self._run_middleware_after(ctx, result)
                finally:
                    context_var.reset(current_token)

                dur = int((time.monotonic() - started) * 1000)
                ctx.logger.info("agent 完成 name=%s duration=%dms", self.name, dur)
                result.extra.setdefault("duration_ms", dur)
                return result
            except Exception:
                ctx.logger.exception("agent 失败 name=%s", self.name)
                raise
        finally:
            context_var.reset(context_token)

    async def stream(self, message: str, **kwargs: Any) -> AsyncIterator[str]:
        """流式输出(SSE)。默认实现:调后端 stream。"""
        from app.harness.communication.blackboard_var import use_blackboard

        ctx = AgentRunContext(
            agent_name=self.name,
            messages=self._build_messages(message),
        )
        self._context = ctx
        context_var = self._run_context_var()
        context_token = context_var.set(ctx)
        try:
            with use_blackboard(ctx.blackboard):
                ctx = await self._run_middleware_before(ctx)
            self._context = ctx
            current_token = context_var.set(ctx)
            try:
                with use_blackboard(ctx.blackboard):
                    async for chunk in self._stream_backend(ctx):
                        yield chunk
            finally:
                context_var.reset(current_token)
        finally:
            context_var.reset(context_token)

    # ============ 拓扑执行(子类实现)============
    async def _execute_topology(self, ctx: AgentRunContext) -> AgentResult:
        """执行拓扑特定逻辑。子类(拓扑基类)实现。"""
        # BaseAgent 默认=single:直接调后端
        text = await self._invoke_backend(ctx)
        return AgentResult(output=text)

    async def _stream_backend(self, ctx: AgentRunContext) -> AsyncIterator[str]:
        """流式调后端。"""
        backend = self._get_backend()
        async for chunk in backend.stream(ctx):
            yield chunk

    # ============ 后端调用 ============
    async def _invoke_backend(self, ctx: AgentRunContext) -> str:
        """根据 self.backend 调对应兼容后端(deepagents/llm)。"""
        backend = self._get_backend()
        return await backend.invoke(ctx)

    def _get_backend(self):
        """惰性构建后端对象(首次用时建,缓存)。同时确保工具已发现。"""
        if self._backend_obj is None:
            from app.harness.backends import build_backend
            from app.harness.tools import discover_tools

            discover_tools()  # 首次用时扫描工具模块(触发 @tool 注册)
            self._backend_obj = build_backend(self)
        return self._backend_obj

    # ============ 中间件 ============
    async def _run_middleware_before(self, ctx: AgentRunContext) -> AgentRunContext:
        """中间件 before(洋葱:按声明顺序)。"""
        from app.harness.middleware import get_pipeline

        pipeline = get_pipeline()
        for name in self.middleware:
            mw = pipeline.get(name)
            if mw is not None:
                try:
                    ctx = await mw.before_invoke(ctx, self)
                except Exception:
                    log.exception("中间件 before 失败 name=%s", name)
        return ctx

    async def _run_middleware_after(self, ctx: AgentRunContext, result: AgentResult) -> AgentResult:
        """中间件 after(洋葱:逆序)。"""
        from app.harness.middleware import get_pipeline

        pipeline = get_pipeline()
        for name in reversed(self.middleware):
            mw = pipeline.get(name)
            if mw is not None:
                try:
                    result = await mw.after_invoke(ctx, self, result)
                except Exception:
                    log.exception("中间件 after 失败 name=%s", name)
        return result

    # ============ 通讯 API(业务直接调)============
    @property
    def context(self) -> AgentRunContext:
        """当前运行上下文(含通讯设施)。"""
        current = self._run_context_var().get()
        if current is not None:
            return current
        if self._context is None:
            raise RuntimeError("agent 尚未运行(context 未注入)")
        return self._context

    # ---- 黑板 ----
    def write(self, key: str, value: Any) -> None:
        """写到共享黑板。"""
        self.context.blackboard.write(key, value, self.name)

    def read(self, key: str) -> Any | None:
        """从共享黑板读。"""
        return self.context.blackboard.read(key)

    def has(self, key: str) -> bool:
        """黑板 key 是否存在。"""
        return self.context.blackboard.has(key)

    # ---- 消息 ----
    async def send(self, target: str, content: str) -> None:
        """给其它 agent 发消息(通知模式)。"""
        await self.context.message_bus.send(target, content, self.name)

    async def request(self, target: str, content: str) -> str:
        """给其它 agent 发消息并等回复(请求-响应模式)。"""
        return await self.context.message_bus.request(target, content, self.name)

    async def reply(self, original_msg_id: str, content: str) -> None:
        """回复一条 request 消息。"""
        await self.context.message_bus.reply(original_msg_id, content, self.name)

    def receive_messages(self) -> list:
        """取收件箱消息(取出后清空)。"""
        return self.context.message_bus.receive(self.name)

    # ---- 事件 ----
    async def publish(self, event_type: str, data: Any = None) -> None:
        """发布事件。"""
        await self.context.event_bus.publish(event_type, data, self.name)

    def subscribe(self, event_type: str, handler: Any) -> None:
        """订阅事件。"""
        self.context.event_bus.subscribe(event_type, handler)

    def setup(self) -> None:
        """初始化钩子(子类重写)。在 run() 前调,适合订阅事件等。
        默认空。"""
        pass

    # ============ 辅助 ============
    def _build_messages(self, message: str) -> list[Message]:
        """构造消息列表:system_prompt + user。"""
        msgs: list[Message] = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        elif self.prompt_file:
            try:
                from app.core.prompt import render_prompt

                rendered = render_prompt(self.prompt_file)
                if rendered.system:
                    msgs.append({"role": "system", "content": rendered.system})
                msgs.append({"role": "user", "content": rendered.user or message})
                return msgs
            except Exception:
                log.exception("加载 prompt_file '%s' 失败", self.prompt_file)
        msgs.append({"role": "user", "content": message})
        return msgs

    def _run_member(self, agent_cls: type[BaseAgent], message: str, ctx: AgentRunContext) -> Any:
        """运行成员 agent(复合拓扑用)。

        实例化成员 agent,共享 context(含通讯设施),跑 run()。
        返回 coroutine(由调用方 await)。
        """

        async def _do():
            member = agent_cls()
            member_ctx = AgentRunContext(
                agent_name=member.name or agent_cls.__name__,
                messages=member._build_messages(message),
                session_id=ctx.session_id,
                source="internal",
                parent_run_id=ctx.run_id,
                depth=ctx.depth + 1,
                user_id=ctx.user_id,
                blackboard=ctx.blackboard,
                message_bus=ctx.message_bus,
                event_bus=ctx.event_bus,
                logger=ctx.logger,
                extra=dict(ctx.extra),
            )
            member._context = member_ctx
            member.setup()
            return await member.run(message, source="internal", context=member_ctx)

        return _do()


__all__ = ["BaseAgent"]
