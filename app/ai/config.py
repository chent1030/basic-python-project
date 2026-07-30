"""单 agent 配置:从 app/ai/agents/<name>/config.yml 加载。

去中心化设计:每个 agent 一个独立目录,内含 config.yml 声明:
- topology: 拓扑(single/subagent/parallel/sequential/conversational/router/pipeline)
- backend:  后端(deepagents/agentscope);single 之外由 topology 隐含
- provider: 用哪个 LLM provider(复用 config 的 llm.providers)
- mode:     trigger(一次性)/ chat(持续对话)
- tools / subagents / members / routes / steps: 拓扑特定字段
- middleware: 挂载的中间件(四类记忆 + tracing + filter)

全局开关/存储位置在 app.core.config.AgentsConfig,不在此处。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# 7 种拓扑
Topology = Literal[
    "single", "subagent", "parallel", "sequential",
    "conversational", "router", "pipeline",
]
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


class PipelineStep(BaseModel):
    """pipeline 拓扑的一个步骤(显式声明)。

    支持两种步骤:
    1) 单 agent 顺序步骤:run = "agent_a"(单个 agent 名)
    2) 并行步骤:parallel = ["b1", "b2", "b3"](多个 agent 同时跑)
       并行步骤可选 aggregator:合并方式(见 app/ai/aggregators)
       - 内置:merge(拼接,默认)/ list(JSON)/ first(取首条)
       - 自定义:在 agent 目录的 aggregator.py 用 @aggregator 定义,填函数名

    name: 步骤名(可选,用于日志/记录)
    input_from: 可选,显式指定本步骤输入来自哪个前序步骤的输出(默认用上一步输出)

    示例(A→[B,C,D并行]→E):
        steps:
          - name: step_a
            run: agent_a
          - name: step_bcd
            parallel: [b1, b2, b3]
            aggregator: merge
          - name: step_e
            run: agent_e
    """

    name: str = ""
    run: str = ""                       # 单 agent 顺序步骤
    parallel: list[str] = Field(default_factory=list)  # 并行步骤(多个 agent)
    aggregator: str = "merge"           # 并行步骤的合并方式
    input_from: str = ""                # 可选:显式指定输入来源步骤名
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

    # skill 目录路径列表(每个目录含 SKILL.md,遵循 Agent Skills 规范)
    # skill 是「教 agent 方法论的指令文档」,比 tool 更高层:
    # tool 是可执行函数,skill 是指导 agent 如何用 tool/完成任务的说明书。
    # 路径相对项目根,如 ["skills/code_review"]。
    # deepagents:传给 create_deep_agent(skills=[...])
    # agentscope:用 LocalSkillLoader(directory=...) 加入 Toolkit
    skills: list[str] = Field(default_factory=list)

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
    # 拓扑:pipeline 步骤列表(顺序执行,某步可 parallel 并行)
    steps: list[PipelineStep] = Field(default_factory=list)

    middleware: list[MiddlewareSpec] = Field(default_factory=list)

    # 该 agent 的目录路径(运行时填充,供加载专属 tools.py)
    dir: str = ""

    def resolved_skill_paths(self) -> list[str]:
        """把 config 里的 skill 相对路径解析成绝对路径(相对项目根)。

        skill 路径在 config.yml 里写成相对项目根(如 "skills/code_review"),
        这里转成绝对路径供 deepagents/agentscope 加载。
        不存在的路径会被跳过(记 warning 由调用方处理)。
        """
        if not self.skills:
            return []
        # 项目根:本文件 app/ai/config.py -> parents[2]
        root = Path(__file__).resolve().parents[2]
        out: list[str] = []
        for s in self.skills:
            p = Path(s)
            if not p.is_absolute():
                p = root / p
            out.append(str(p))
        return out

    @classmethod
    def from_yaml_file(cls, path: Path) -> AgentConfig:
        """从 config.yml 加载。dir 字段填为该文件所在目录。"""
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw["dir"] = str(path.parent)
        return cls.model_validate(raw)


__all__ = ["Topology", "Backend", "Mode", "MiddlewareSpec", "AgentConfig"]
