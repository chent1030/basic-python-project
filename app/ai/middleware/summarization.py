"""summarization 中间件 —— 对话历史过长时自动摘要压缩。

before_invoke:历史消息数 > max_messages 时,把旧历史用 LLM 摘要成一段,
保留最近 keep_recent 条不摘要,避免上下文超限。
after_invoke: 不做事(只读压缩,不影响结果)。

与 session_memory 配合:session 存原始消息(完整),summarization 在喂给模型前压缩。
这样存储完整、调用时省 token。

走 app.services.llm(同一 provider),不绑后端,任何 agent 都能挂。

config.yml:
    middleware:
      - name: summarization
        config:
          max_messages: 20      # 超过这么多条就摘要(默认)
          keep_recent: 4        # 保留最近几条不摘要(默认)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.base import AgentResult
from app.ai.middleware.base import MiddlewareBase
from app.core.logging_config import get_logger

if TYPE_CHECKING:
    from app.ai.base import AgentRunContext
    from app.ai.config import AgentConfig

log = get_logger("app.ai.middleware.summarization")

_DEFAULT_MAX = 20
_DEFAULT_KEEP = 4


def _spec_config(specs: list, name: str) -> dict:
    """从 agent config.middleware 里取某中间件的 config。"""
    for s in specs:
        if s.name == name:
            return s.config or {}
    return {}


class SummarizationMiddleware(MiddlewareBase):
    name = "summarization"

    async def before_invoke(self, ctx: AgentRunContext, cfg: AgentConfig) -> AgentRunContext:
        sc = _spec_config(cfg.middleware, "summarization")
        max_messages = sc.get("max_messages", _DEFAULT_MAX)
        keep_recent = sc.get("keep_recent", _DEFAULT_KEEP)

        msgs = ctx.messages
        if len(msgs) <= max_messages:
            return ctx  # 没超限,不摘要

        # 拆分:system 消息保留;旧历史(摘要);最近 keep_recent 条(原样)
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        non_system = [m for m in msgs if m.get("role") != "system"]
        to_summarize = non_system[: len(non_system) - keep_recent]
        recent = non_system[len(non_system) - keep_recent:]

        if not to_summarize:
            return ctx

        # 用 LLM 摘要旧历史
        summary = await self._summarize(to_summarize, cfg)
        if not summary:
            return ctx  # 摘要失败就不动

        ctx.messages = system_msgs + [
            {"role": "system", "content": f"[历史摘要]\n{summary}"}
        ] + recent
        ctx.logger.info(
            "[summarization] 压缩历史: %d 条 → 摘要 + 最近 %d 条",
            len(to_summarize), len(recent),
        )
        return ctx

    async def after_invoke(
        self, ctx: AgentRunContext, cfg: AgentConfig, result: AgentResult
    ) -> AgentResult:
        return result  # 只读压缩,不改结果

    async def _summarize(self, messages: list[dict], cfg: AgentConfig) -> str:
        """用 LLM 把一段对话历史摘要成文本。失败返回空串。"""
        try:
            from app.services.llm import llm as llm_svc

            history_text = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
            )
            prompt = (
                f"把以下对话历史压缩成简洁摘要,保留关键信息和上下文,不要遗漏重要细节:\n\n"
                f"{history_text}"
            )
            from langchain_core.messages import HumanMessage

            return await llm_svc.invoke(
                [HumanMessage(content=prompt)],
                provider=cfg.provider or None,
            )
        except Exception:
            log.debug("[summarization] 摘要失败(数据源/LLM 可能不可用),跳过压缩", exc_info=True)
            return ""


__all__ = ["SummarizationMiddleware"]
