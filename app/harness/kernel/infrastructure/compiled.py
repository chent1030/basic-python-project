from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from ..domain.models import Conflict, callable_version, digest
from ..domain.reviews import resume_values


@dataclass(frozen=True)
class CompiledAgent:
    graph_factory: Any
    output_mapper: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "graph_factory": callable_version(self.graph_factory),
            "output_mapper": callable_version(self.output_mapper),
        }

    async def ainvoke(self, inputs: Any, context: Any, resume: Any = None) -> Any:
        root = context.runtime.workspace_root.resolve().parent / "checkpoints"
        root.mkdir(parents=True, exist_ok=True)
        key = digest(vars(context.scope))
        async with AsyncSqliteSaver.from_conn_string(str(root / f"compiled-{key}.sqlite")) as saver:
            graph = self.graph_factory(context, saver)
            if graph.checkpointer is not saver:
                raise ValueError("Compiled graph must use the supplied durable checkpointer")
            config = {"configurable": {"thread_id": key}}
            supplied = Command(resume=resume_values(resume)) if resume is not None else inputs
            async for event in graph.astream(supplied, config=config, stream_mode="updates"):
                context.emit("compiled.update", {"nodes": list(event)})
            state = await graph.aget_state(config)
            interruptions = [item for task in state.tasks for item in task.interrupts]
            if interruptions:
                context.runtime.interrupt(
                    context,
                    {
                        "interrupts": [
                            {"id": item.id, "value": item.value} for item in interruptions
                        ]
                    },
                )
            if state.next:
                raise Conflict("Compiled graph stopped without completion")
            return self.output_mapper(state.values) if self.output_mapper else state.values
