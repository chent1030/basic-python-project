from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..domain.models import Conflict
from .runtime import Runtime
from .services import Observer


class Worker:
    def __init__(
        self,
        runtime: Runtime,
        *,
        concurrency: int = 4,
        interval: float = 0.5,
        observers: tuple[Observer, ...] = (),
    ):
        if concurrency < 1 or interval <= 0:
            raise ValueError("Worker limits must be positive")
        self.runtime, self.concurrency, self.interval = runtime, concurrency, interval
        self.observers = observers
        self.jobs: dict[tuple[str, str], asyncio.Task] = {}
        self.stopping = asyncio.Event()
        self.loop_task: asyncio.Task | None = None
        self.observer_task: asyncio.Task | None = None
        self.log = logging.getLogger(__name__)

    def start(self) -> None:
        if self.loop_task is not None:
            raise Conflict("Worker already started")
        self.loop_task = asyncio.create_task(self.serve(), name="agent-worker")
        self.observer_task = asyncio.create_task(self.observe(), name="agent-observers")

    async def stop(self, *, grace_seconds: float = 10) -> None:
        self.stopping.set()
        tasks = [task for task in (self.loop_task, self.observer_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.jobs:
            _, pending = await asyncio.wait(list(self.jobs.values()), timeout=grace_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        self.jobs.clear()
        self.loop_task = self.observer_task = None

    def ready(self, tenant: str, run: dict[str, Any]) -> bool:
        if run["lease_until"] > time.time():
            return False
        if run["status"] in ("queued", "running"):
            return True
        if run["status"] != "waiting":
            return False
        return not any(
            record["run_id"] == run["id"]
            and record["state"] == "pending"
            and record["expires"] > time.time()
            for record in self.runtime.repository.scan(tenant, "approval")
        )

    async def tick(self) -> None:
        for key, task in list(self.jobs.items()):
            if task.done():
                try:
                    task.result()
                except Exception:
                    self.log.exception("Agent worker job failed: %s", key)
                self.jobs.pop(key)
        for tenant in self.runtime.repository.tenants():
            for run in self.runtime.repository.scan(tenant, "run"):
                key = (tenant, run["id"])
                if len(self.jobs) >= self.concurrency:
                    return
                if key not in self.jobs and self.ready(tenant, run):
                    self.jobs[key] = asyncio.create_task(self._execute(tenant, run["id"]))

    async def _execute(self, tenant: str, run_id: str) -> None:
        try:
            await self.runtime.execute(tenant, run_id)
        except Conflict as exc:
            run = self.runtime.get(tenant, run_id)
            if run["lease_until"] > time.time():
                return
            with self.runtime.repository.transaction():
                run = self.runtime.get(tenant, run_id)
                if run["lease_until"] > time.time():
                    return
                run.update(status="uncertain", error=str(exc), error_type=type(exc).__name__)
                self.runtime.repository.put(tenant, "run", run_id, run)
                self.runtime.repository.event(
                    tenant, run_id, "run.configuration_conflict", {"error_type": type(exc).__name__}
                )

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=self.interval)
        except TimeoutError:
            return

    async def serve(self) -> None:
        while not self.stopping.is_set():
            try:
                await self.tick()
            except Exception:
                self.log.exception("Agent worker poll failed")
            await self._wait()

    async def observe(self) -> None:
        while not self.stopping.is_set():
            for observer in self.observers:
                for tenant in self.runtime.repository.tenants():
                    for run in self.runtime.repository.scan(tenant, "run"):
                        if run.get("purpose") == "observer" and not getattr(
                            observer, "include_observer_runs", False
                        ):
                            continue
                        try:
                            async with asyncio.timeout(30):
                                await observer.drain(tenant, run["id"])
                        except Exception:
                            self.log.exception("Observer consumer failed: %s", observer.name)
            await self._wait()
