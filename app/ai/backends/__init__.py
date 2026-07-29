"""后端适配器 —— 把统一抽象翻译成 deepagents / agentscope 各自的输入输出。

每个 backend 实现 BaseBackend(invoke/stream),内部把:
- provider 配置 → 该库的模型对象
- 工具(ToolDef)→ 该库的工具格式
- messages → 该库的消息格式
- 输出 → AgentResult

gateway/runners 只面向 BaseBackend,不直接接触具体库。
"""
from __future__ import annotations

from app.ai.base import BaseBackend
from app.ai.config import AgentConfig


def build_backend(cfg: AgentConfig) -> BaseBackend:
    """按 config 的 backend 字段选适配器。

    single 拓扑直接按 backend 选;其它拓扑(sequential/parallel/...)
    内部成员各自选 backend,由 runner 处理,这里只服务 single/subagent。
    """
    if cfg.backend == "deepagents":
        from app.ai.backends.deepagents_backend import DeepAgentsBackend

        return DeepAgentsBackend(cfg)
    if cfg.backend == "agentscope":
        from app.ai.backends.agentscope_backend import AgentScopeBackend

        return AgentScopeBackend(cfg)
    raise ValueError(f"未知 backend: {cfg.backend}(deepagents | agentscope)")


__all__ = ["build_backend"]
