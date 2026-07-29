"""单 agent 配置:从 app/ai/agents/<name>/config.yml 加载。

去中心化设计:每个 agent 一个独立目录,内含 config.yml 声明:
- topology: 拓扑(single/subagent/parallel/sequential/conversational/router)
- backend:  后端(deepagents/agentscope);single 之外由 topology 隐含
- provider: 用哪个 LLM provider(复用 config 的 llm.providers)
- mode:     trigger(一次性)/ chat(持续对话)
- tools / subagents / members / routes: 拓扑特定字段
- middleware: 挂载的中间件(四类记忆 + tracing + filter)

全局开关/存储位置在 app.core.config.AgentsConfig,不在此处。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# 6 种拓扑
Topology = Literal["single", "subagent", "parallel", "sequential", "conversational", "router"]
# 后端
Backend = Literal["deepagents", "agentscope"]
# 调用模式
Mode = Literal["trigger", "chat"]


class MiddlewareSpec(BaseModel):
    """agent config.yml 里 middleware 列表的一项。

    name:   中间件名(对应 app/ai/middleware/ 下的模块)
    config: 该中间件的配置(可选,覆盖中间件默认)
    """

    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """单个 agent 的配置(从 agents/<name>/config.yml 解析)。

    拓扑特定字段:
    - single/subagent: backend + provider + system_prompt + tools
    - subagent:        subagents=[引用其它 agent 名]
    - parallel:        members + aggregator(merge/list)
    - sequential:      members
    - conversational:  members + rounds
    - router:          routes(意图->agent 名)
    """

    topology: Topology = "single"
    backend: Backend = "deepagents"
    provider: str = ""  # 空=用 llm.default_provider
    mode: Mode = "trigger"
    system_prompt: str = ""
    prompt_file: str = ""  # 可选:引用 prompts/ 下文件作为 system
    tools: list[str] = Field(default_factory=list)  # 全局工具名;专属工具自动加载
    model: str | None = None  # 覆盖 provider 默认 model
    temperature: float | None = None
    max_tokens: int | None = None
    recursion_limit: int = 25  # 执行步数上限

    # 拓扑:子 agent(deepagents)
    subagents: list[str] = Field(default_factory=list)
    # 拓扑:成员 agent(parallel/sequential/conversational)
    members: list[str] = Field(default_factory=list)
    # 拓扑:parallel 汇总策略
    aggregator: str = "merge"  # merge(拼接) | list(列表) | first(取首条)
    # 拓扑:conversational 轮数
    rounds: int = 3
    # 拓扑:router 路由表(意图 -> agent 名)
    routes: dict[str, str] = Field(default_factory=dict)

    middleware: list[MiddlewareSpec] = Field(default_factory=list)

    # 该 agent 的目录路径(运行时填充,供加载专属 tools.py)
    dir: str = ""

    @classmethod
    def from_yaml_file(cls, path: Path) -> AgentConfig:
        """从 config.yml 加载。dir 字段填为该文件所在目录。"""
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw["dir"] = str(path.parent)
        return cls.model_validate(raw)


__all__ = ["Topology", "Backend", "Mode", "MiddlewareSpec", "AgentConfig"]
