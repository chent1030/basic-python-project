from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from typing import Any, Literal

from langchain_core.messages import AIMessage, messages_from_dict, messages_to_dict
from langchain_core.runnables import RunnableLambda
from langgraph.types import Command, interrupt

from ..application.runtime import ExecutionContext
from ..domain.models import AgentDefinition, Conflict, Waiting, digest
from ..domain.reviews import resume_values
from .interpreter import adapt_interpreter
from .middleware import ExecutionAudit, current_tool_call
from .models import Models, apply_profile
from .store import ScopedStore


@dataclass(frozen=True)
class SubAgent:
    definition: AgentDefinition
    description: str
    mode: Literal["isolated", "fork"] = "isolated"
    input_mapper: Any = None
    allow_context_fork: bool = False

    @property
    def name(self) -> str:
        return self.definition.name

    def __post_init__(self) -> None:
        if self.mode == "fork" and (not self.allow_context_fork or self.definition.skills):
            raise ValueError("fork requires explicit context authorization and no child skills")


class DeepAgentsEngine:
    def __init__(self, models: Models, *, model_factory: Any = None, profile: Any = None):
        self.models, self.model_factory, self.profile = models, model_factory, profile

    def snapshot(self, definition: AgentDefinition) -> dict[str, Any]:
        return self.models.snapshot(definition)

    async def invoke(
        self,
        definition: AgentDefinition,
        inputs: Any,
        context: ExecutionContext,
        resume: Any = None,
    ) -> Any:
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
        from langchain.agents.middleware import (
            ModelCallLimitMiddleware,
            ModelFallbackMiddleware,
            ToolCallLimitMiddleware,
        )
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        snapshot = self.snapshot(definition)
        record = context.runtime.repository.get(
            context.scope.tenant_id, "invocation", context.scope.invocation_id
        )
        if record.get("model_snapshot") and record["model_snapshot"] != snapshot:
            raise Conflict("Model configuration changed during checkpoint resume")
        record["model_snapshot"] = snapshot
        context.runtime.repository.put(
            context.scope.tenant_id, "invocation", context.scope.invocation_id, record
        )
        model = (
            self.model_factory(definition) if self.model_factory else self.models.create(definition)
        )
        model = apply_profile(
            model, planning=definition.planning, general_purpose=False, profile=self.profile
        )
        workspace = context.workspace
        backend = (
            definition.backend_factory(context)
            if definition.backend_factory
            else FilesystemBackend(root_dir=workspace.root, virtual_mode=True)
        )
        children = list(definition.subagents)
        if definition.general_purpose:
            child = replace(
                definition,
                name=f"{definition.name}_general",
                general_purpose=False,
                subagents=(),
                output_schema=None,
            )
            children.append(SubAgent(child, "Delegate a bounded independent task."))
        subagents = [self._subagent(child, context) for child in children]
        engine_root = context.runtime.workspace_root.resolve().parent / "checkpoints"
        engine_root.mkdir(parents=True, exist_ok=True)
        checkpointer_path = engine_root / f"{digest(vars(context.scope))}.sqlite"
        config = {
            "configurable": {"thread_id": digest(vars(context.scope))},
            "recursion_limit": definition.recursion_limit,
            "metadata": {
                "tenant_id": context.scope.tenant_id,
                "run_id": context.scope.run_id,
                "invocation_id": context.scope.invocation_id,
            },
        }
        async with AsyncExitStack() as resources:
            checkpointer = await resources.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(checkpointer_path)),
            )
            tools = list(definition.tools)
            for toolkit in definition.toolkits:
                tools.extend(await resources.enter_async_context(toolkit(context)))
            middleware = [
                ExecutionAudit(context),
                ModelCallLimitMiddleware(
                    thread_limit=definition.max_model_calls, exit_behavior="error"
                ),
                ToolCallLimitMiddleware(
                    thread_limit=definition.max_tool_calls, exit_behavior="error"
                ),
                *(
                    adapt_interpreter(item, tools, definition, context)
                    for item in definition.middleware
                ),
            ]
            if snapshot["fallbacks"]:
                middleware.append(
                    ModelFallbackMiddleware(
                        *[
                            self.models.create(definition, fallback["profile"])
                            for fallback in snapshot["fallbacks"]
                        ]
                    )
                )
            graph = create_deep_agent(
                model=model,
                tools=tools,
                system_prompt=definition.system_prompt,
                middleware=middleware,
                subagents=subagents,
                skills=list(definition.skills) or None,
                memory=list(definition.memory) or None,
                permissions=list(definition.permissions) or None,
                backend=backend,
                interrupt_on=definition.interrupt_on or None,
                response_format=definition.output_schema,
                state_schema=definition.state_schema,
                context_schema=definition.context_schema,
                checkpointer=checkpointer,
                store=ScopedStore(
                    context.runtime.repository, context.scope.tenant_id, context.scope.run_id
                ),
                name=definition.name,
                cache=definition.cache_factory(context.scope) if definition.cache_factory else None,
            )
            context.emit(
                "engine.capabilities",
                {
                    "model": snapshot,
                    "subagents": [child.name for child in children],
                    "tools": [getattr(tool, "name", str(tool)) for tool in tools],
                    "middleware": [type(item).__name__ for item in middleware],
                    "backend": type(backend).__qualname__,
                    "checkpoint_thread": config["configurable"]["thread_id"],
                    "graph_nodes": list(graph.get_graph().nodes),
                },
            )
            if resume is None:
                prompt = json.dumps(inputs, ensure_ascii=False)
                graph_input: Any = {"messages": [{"role": "user", "content": prompt}]}
                if context.inherited_messages is not None:
                    graph_input["messages"] = messages_from_dict(context.inherited_messages)
                if definition.initial_state_factory:
                    graph_input.update(definition.initial_state_factory(inputs, context))
            else:
                graph_input = Command(resume=self._resume(resume))
            async for namespace, mode, chunk in graph.astream(
                graph_input,
                config=config,
                stream_mode=["updates", "messages", "custom"],
                subgraphs=True,
                context=definition.context_factory(context)
                if definition.context_factory
                else context,
            ):
                context.emit("engine.stream", self._event(namespace, mode, chunk))
            state = await graph.aget_state(config)
            interruptions = [item for task in state.tasks for item in task.interrupts]
            if interruptions:
                context.runtime.interrupt(
                    context,
                    {
                        "interrupts": [
                            {"id": item.id, "value": item.value} for item in interruptions
                        ],
                    },
                )
            if state.next:
                raise Conflict("Graph stopped without a final result or recognized interrupt")
            if "structured_response" in state.values:
                return state.values["structured_response"]
            if definition.output_schema:
                raise ValueError("Model returned no structured response")
            messages = state.values.get("messages", [])
            if not messages or not isinstance(messages[-1], AIMessage):
                raise ValueError("Graph returned no final assistant message")
            return messages[-1].content

    def _subagent(self, binding: SubAgent, context: ExecutionContext) -> dict[str, Any]:
        if not isinstance(binding, SubAgent):
            raise TypeError("Use governed SubAgent bindings, not raw SDK subagent dictionaries")
        definition = binding.definition
        registered = context.runtime.registry.get(definition.name)
        if registered and registered != definition:
            raise Conflict("Conflicting native subagent definition")
        context.runtime.registry[definition.name] = definition

        async def invoke(state: dict[str, Any], config: Any) -> dict[str, Any]:
            messages = state.get("messages", [])
            payload = (
                binding.input_mapper(state)
                if binding.input_mapper
                else {
                    "messages": messages_to_dict(messages),
                }
            )
            group = digest(
                [
                    definition.name,
                    payload,
                    current_tool_call.get()
                    or config.get("metadata", {}).get("langgraph_checkpoint_ns"),
                ]
            )
            ordinal = context.dispatch_counters.get(group, 0)
            context.dispatch_counters[group] = ordinal + 1
            key = "native_" + digest([group, ordinal])[:32]
            try:
                output = await context.request(
                    definition.name,
                    payload,
                    key=key,
                    inherited_messages=messages_to_dict(messages)
                    if binding.mode == "fork"
                    else None,
                )
            except Waiting as exc:
                interrupt({"framework_approval": str(exc)})
                output = await context.request(
                    definition.name,
                    payload,
                    key=key,
                    inherited_messages=messages_to_dict(messages)
                    if binding.mode == "fork"
                    else None,
                )
            return {"messages": [AIMessage(content=json.dumps(output, ensure_ascii=False))]}

        return {
            "name": binding.name,
            "description": binding.description,
            "mode": binding.mode,
            "runnable": RunnableLambda(invoke),
        }

    @staticmethod
    def _resume(payload: dict[str, Any]) -> Any:
        return resume_values(payload)

    @staticmethod
    def _event(namespace: Any, mode: str, chunk: Any) -> dict[str, Any]:
        event: dict[str, Any] = {"namespace": list(namespace), "mode": mode}
        if mode == "messages":
            message, metadata = chunk
            event.update(
                message_type=message.type,
                node=metadata.get("langgraph_node"),
                tool_calls=[call.get("name") for call in getattr(message, "tool_calls", [])],
                usage=getattr(message, "usage_metadata", None),
            )
        elif isinstance(chunk, dict):
            event["keys"] = list(chunk)
        else:
            event["payload_type"] = type(chunk).__name__
        return event
