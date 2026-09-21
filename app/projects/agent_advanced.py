from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from langchain.agents.middleware import PIIMiddleware
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.harness.kernel import (
    AgentDefinition,
    Approval,
    CompiledAgent,
    DockerBackend,
    MCPTools,
    RemoteAgent,
    Resources,
    Runtime,
    Step,
    SubAgent,
    attach_memory_observer,
)
from app.projects.agent_examples import FACTS_PROMPT, DocumentInput, Findings


class ReviewState(TypedDict):
    document: str
    reviewed: str


def review_graph(context, checkpointer):
    def review(state: ReviewState):
        reviewed = interrupt({"question": "请确认或修订本文档", "document": state["document"]})
        return {"reviewed": reviewed}

    graph = StateGraph(ReviewState)
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)


def child_input(state):
    return {"document": state["messages"][-1].content}


def register(runtime: Runtime) -> None:
    root = Path(__file__).with_name("agent_resources")
    resources = Resources(
        {"/skills/": str(root / "skills"), "/reference/": str(root / "reference")}
    )
    reader = AgentDefinition(
        "resource_reader",
        system_prompt=FACTS_PROMPT,
        input_schema=DocumentInput,
        output_schema=Findings,
        skills=("/skills/",),
        memory=("/reference/guidelines.md",),
        backend_factory=resources,
        middleware=(PIIMiddleware("email", apply_to_input=True, apply_to_output=True),),
    )
    runtime.register("resource_analysis", Step("read", reader))
    child = AgentDefinition(
        "native_reader",
        system_prompt=FACTS_PROMPT,
        input_schema=DocumentInput,
        output_schema=Findings,
        approval=Approval.before(),
    )
    coordinator = AgentDefinition(
        "native_coordinator",
        model_profile="reasoning_large",
        system_prompt="把输入文档完整委派给 native_reader，保留其证据和局限，不捏造结果。"
        "子调用需要人工批准，你不能替人批准，也不能跳过批准自行生成子调用结果。",
        subagents=(SubAgent(child, "抽取有原文证据支持的事实", input_mapper=child_input),),
    )
    runtime.register("native_review", Step("delegate", coordinator))
    dynamic = AgentDefinition(
        "dynamic_coordinator",
        model_profile="reasoning_large",
        system_prompt="用 JavaScript task() 向 native_reader 委派独立的文本分析任务。"
        "每个委派提供完整资料，最多同时两个子任务。不得伪造审批或子任务结果。",
        subagents=(SubAgent(child, "有审批约束的事实抽取", input_mapper=child_input),),
        middleware=(
            CodeInterpreterMiddleware(max_ptc_calls=16, timeout=3, memory_limit=32_000_000),
        ),
    )
    runtime.register("dynamic_review", Step("delegate", dynamic))
    runtime.register(
        "compiled_review",
        Step(
            "review",
            AgentDefinition(
                "compiled_reviewer",
                handler=CompiledAgent(review_graph),
                input_schema=DocumentInput,
            ),
        ),
    )
    if os.environ.get("AGENT_REMOTE_URL"):
        runtime.register(
            "remote_review",
            Step(
                "remote",
                AgentDefinition(
                    "remote_reviewer",
                    handler=RemoteAgent(
                        os.environ["AGENT_REMOTE_URL"],
                        os.environ["AGENT_REMOTE_GRAPH"],
                    ),
                ),
            ),
        )
    if os.environ.get("AGENT_MCP_URL"):
        runtime.register(
            "mcp_search",
            Step(
                "search",
                AgentDefinition(
                    "mcp_searcher",
                    system_prompt="仅用已授权的搜索工具检索输入问题，引用真实结果，"
                    "无结果时明确说明，不得捏造引用或执行写入。",
                    toolkits=(
                        MCPTools(
                            {
                                "documents": {
                                    "transport": "streamable_http",
                                    "url": os.environ["AGENT_MCP_URL"],
                                }
                            },
                            ("documents__search",),
                        ),
                    ),
                ),
            ),
        )
    if os.environ.get("AGENT_SANDBOX_IMAGE"):
        runtime.register(
            "sandbox_analysis",
            Step(
                "sandbox",
                AgentDefinition(
                    "sandbox_worker",
                    system_prompt="只在已配置沙箱工作目录处理用户提供的数据。"
                    "禁止网络、读取宿主机数据、获取密钥和改变安全策略。",
                    backend_factory=DockerBackend(os.environ["AGENT_SANDBOX_IMAGE"]),
                    interrupt_on={"execute": True},
                ),
            ),
        )
    if os.environ.get("AGENT_MEMORY_OBSERVER", "false").lower() == "true":
        attach_memory_observer(runtime)
