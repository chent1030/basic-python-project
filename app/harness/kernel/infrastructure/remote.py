from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..domain.models import Cancelled, Conflict, Uncertain, digest


@dataclass(frozen=True)
class RemoteAgent:
    url: str
    graph_id: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    poll_seconds: float = 1
    client_factory: Any = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {"url": self.url, "graph_id": self.graph_id, "poll_seconds": self.poll_seconds}

    async def ainvoke(self, inputs: Any, context: Any, resume: Any = None) -> Any:
        from langgraph_sdk import get_client

        client = (
            self.client_factory()
            if self.client_factory
            else get_client(
                url=self.url,
                api_key=None,
                headers={
                    key: os.environ[value[4:]] if value.startswith("env:") else value
                    for key, value in self.headers.items()
                },
                timeout=30,
            )
        )
        try:
            return await self._invoke(client, inputs, context, resume)
        finally:
            if not self.client_factory:
                await client.http.client.aclose()

    async def _invoke(self, client: Any, inputs: Any, context: Any, resume: Any) -> Any:
        from ..domain.reviews import resume_values

        repository, scope = context.runtime.repository, context.scope
        operation = digest([scope.invocation_id, resume])
        record = repository.get(scope.tenant_id, "remote", operation)
        if record is None:
            thread_id = str(UUID(digest([scope.tenant_id, scope.run_id, scope.invocation_id])[:32]))
            await client.threads.create(
                thread_id=thread_id,
                if_exists="do_nothing",
                metadata={"tenant_id": scope.tenant_id, "invocation_id": scope.invocation_id},
            )
            record = {
                "id": operation,
                "run_id": scope.run_id,
                "thread_id": thread_id,
                "phase": "submitting",
                "remote_run_id": None,
                "invocation_id": scope.invocation_id,
            }
            repository.put(scope.tenant_id, "remote", operation, record)
            try:
                created = await client.runs.create(
                    thread_id,
                    self.graph_id,
                    input=inputs if resume is None else None,
                    command={"resume": resume_values(resume)} if resume else None,
                    metadata={"framework_operation": operation},
                    multitask_strategy="reject",
                )
            except Exception as exc:
                raise Uncertain(
                    "Remote submission acknowledgement lost; reconcile before retry"
                ) from exc
            record.update(remote_run_id=created["run_id"], phase="running")
            repository.put(scope.tenant_id, "remote", operation, record)
        elif record["remote_run_id"] is None:
            remote_runs = await client.runs.list(record["thread_id"], limit=100)
            candidates = [
                run
                for run in remote_runs
                if run.get("metadata", {}).get("framework_operation") == operation
            ]
            if len(candidates) != 1:
                raise Uncertain("Remote submission cannot be uniquely reconciled")
            record.update(remote_run_id=candidates[0]["run_id"], phase="running")
            repository.put(scope.tenant_id, "remote", operation, record)
        if record["phase"] == "succeeded":
            return record["output"]
        try:
            while True:
                context.runtime.check_cancel(scope)
                remote = await client.runs.get(record["thread_id"], record["remote_run_id"])
                status = remote["status"]
                if status == "success":
                    state = await client.threads.get_state(record["thread_id"])
                    record.update(phase="succeeded", output=state["values"])
                    repository.put(scope.tenant_id, "remote", operation, record)
                    context.emit("remote.succeeded", {"remote_run_id": record["remote_run_id"]})
                    return record["output"]
                if status == "interrupted":
                    state = await client.threads.get_state(record["thread_id"])
                    interrupts = [
                        item
                        for task in state.get("tasks", [])
                        for item in task.get("interrupts", [])
                    ]
                    if not interrupts:
                        raise Conflict("Remote run interrupted without reviewable interrupt data")
                    context.runtime.interrupt(context, {"interrupts": interrupts})
                if status in ("error", "timeout"):
                    raise RuntimeError(f"Remote agent ended with status {status}")
                await asyncio.sleep(self.poll_seconds)
        except (asyncio.CancelledError, Cancelled):
            record["phase"] = "cancel_requested"
            repository.put(scope.tenant_id, "remote", operation, record)
            try:
                async with asyncio.timeout(10):
                    await client.runs.cancel(
                        record["thread_id"], record["remote_run_id"], wait=True
                    )
                    confirmed = await client.runs.get(record["thread_id"], record["remote_run_id"])
                    if confirmed["status"] in ("interrupted", "error", "success", "timeout"):
                        record["phase"] = "cancel_confirmed"
                        repository.put(scope.tenant_id, "remote", operation, record)
            finally:
                raise
