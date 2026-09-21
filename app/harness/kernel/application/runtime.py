from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..domain.composition import (
    Condition,
    Input,
    Loop,
    Output,
    Parallel,
    Sequence,
    Step,
    Supervisor,
    agents_in,
    describe,
    validate_composition,
)
from ..domain.models import (
    AgentDefinition,
    Approval,
    Cancelled,
    Conflict,
    Decision,
    Forbidden,
    Scope,
    Uncertain,
    Waiting,
    digest,
    identity,
    segment,
)
from ..domain.policy import DeploymentPolicy
from ..domain.ports import Engine, Repository
from ..domain.reviews import prepare_tool_resume
from .services import Artifacts, Mailbox, Memory, Workspace


def select(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def bind(spec: Any, inputs: Any, outputs: dict[str, Any]) -> Any:
    if isinstance(spec, Input):
        return select(inputs, spec.path)
    if isinstance(spec, Output):
        return select(outputs[spec.step], spec.path)
    if isinstance(spec, dict):
        return {key: bind(value, inputs, outputs) for key, value in spec.items()}
    if isinstance(spec, (list, tuple)):
        return [bind(value, inputs, outputs) for value in spec]
    return spec


@dataclass
class ExecutionContext:
    runtime: Runtime
    scope: Scope
    path: str
    stack: tuple[str, ...]
    allowed_agents: tuple[str, ...] = ()
    slots: list[Any] = field(default_factory=list)
    requests: int = 0
    inherited_messages: list[Any] | None = None
    dispatch_counters: dict[str, int] = field(default_factory=dict)

    @property
    def agents(self) -> ExecutionContext:
        return self

    @property
    def workspace(self) -> Workspace:
        return Workspace(self.runtime.workspace_root, self.scope)

    @property
    def artifacts(self) -> Artifacts:
        return Artifacts(self.runtime.repository, self.scope)

    @property
    def messages(self) -> Mailbox:
        return Mailbox(self.runtime.repository, self.scope)

    @property
    def memory(self) -> Memory:
        return Memory(self.runtime.repository)

    def emit(self, kind: str, data: dict[str, Any]) -> int:
        self.runtime.check_cancel(self.scope)
        return self.runtime.repository.event(
            self.scope.tenant_id,
            self.scope.run_id,
            kind,
            {
                **data,
                "invocation_id": self.scope.invocation_id,
                "attempt_id": self.scope.attempt_id,
            },
        )

    async def request(
        self, agent: str, inputs: Any, *, key: str, inherited_messages: list[Any] | None = None
    ) -> Any:
        if agent not in self.allowed_agents:
            raise Forbidden("Agent is not in this invocation's delegation allowlist")
        if agent in self.stack or len(self.stack) >= self.runtime.max_depth:
            raise Conflict("Agent request cycle or maximum depth exceeded")
        definition = self.runtime.registry.get(agent)
        if definition is None:
            raise Forbidden("Agent is not in this runtime's registry")
        self.requests += 1
        if self.requests == 1:
            self.release()
        try:
            return await self.runtime._step(
                Step(segment(key), definition, inputs),
                self.scope,
                f"{self.path}/request/{key}",
                inputs,
                {},
                self.stack,
                inherited_messages=inherited_messages,
            )
        finally:
            self.requests -= 1
            if not self.requests and not asyncio.current_task().cancelling():
                await self.acquire()

    async def acquire(self) -> None:
        try:
            for semaphore in self.runtime.capacity(self.scope):
                await semaphore.acquire()
                self.slots.append(semaphore)
            while not self.runtime.reserve_slot(self.scope):
                self.runtime.check_cancel(self.scope)
                await asyncio.sleep(0.025)
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        self.runtime.release_slot(self.scope)
        for semaphore in self.slots:
            semaphore.release()
        self.slots.clear()

    def record_usage(self, usage: dict[str, Any]) -> None:
        with self.runtime.repository.transaction():
            run = self.runtime.get(self.scope.tenant_id, self.scope.run_id)
            run["tokens_used"] = run.get("tokens_used", 0) + max(0, usage.get("total_tokens", 0))
            self.runtime.repository.put(self.scope.tenant_id, "run", self.scope.run_id, run)
            self.runtime.repository.event(
                self.scope.tenant_id,
                self.scope.run_id,
                "model.usage",
                {"invocation_id": self.scope.invocation_id, **usage},
            )
            exhausted = run["tokens_used"] > self.runtime.max_tokens
        if exhausted:
            raise Conflict("Run token budget exhausted")


class Runtime:
    def __init__(
        self,
        repository: Repository,
        engine: Engine | None = None,
        *,
        workspace_root: str | Path = ".agent-data/workspaces",
        max_depth: int = 8,
        max_invocations: int = 1000,
        lease_seconds: int = 30,
        max_concurrency: int = 32,
        tenant_concurrency: int = 16,
        run_concurrency: int = 8,
        max_tokens: int = 500000,
        policy: DeploymentPolicy | None = None,
    ):
        if (
            min(
                max_depth,
                max_invocations,
                lease_seconds,
                max_concurrency,
                tenant_concurrency,
                run_concurrency,
            )
            < 1
        ):
            raise ValueError("Runtime limits must be positive")
        self.repository, self.engine = repository, engine
        self.workspace_root = Path(workspace_root)
        self.max_depth, self.max_invocations, self.lease_seconds = (
            max_depth,
            max_invocations,
            lease_seconds,
        )
        self.workflows: dict[str, tuple[str, Any]] = {}
        self.registry: dict[str, AgentDefinition] = {}
        self.active: dict[tuple[str, str], asyncio.Task] = {}
        self.owners: dict[tuple[str, str], str] = {}
        self.observers: list[Any] = []
        self.max_tokens = max_tokens
        self.policy = policy or DeploymentPolicy()
        self.global_capacity = asyncio.Semaphore(max_concurrency)
        self.global_limit = max_concurrency
        self.tenant_limit, self.run_limit = tenant_concurrency, run_concurrency
        self.tenant_capacity: dict[str, asyncio.Semaphore] = {}
        self.run_capacity: dict[str, asyncio.Semaphore] = {}

    def capacity(self, scope: Scope) -> tuple[Any, ...]:
        tenant = self.tenant_capacity.setdefault(
            scope.tenant_id, asyncio.Semaphore(self.tenant_limit)
        )
        run = self.run_capacity.setdefault(scope.run_id, asyncio.Semaphore(self.run_limit))
        return run, tenant, self.global_capacity

    def reserve_slot(self, scope: Scope) -> bool:
        self.check_cancel(scope)
        with self.repository.transaction():
            active = [
                slot
                for tenant in self.repository.tenants()
                for slot in self.repository.scan(tenant, "capacity")
                if slot["expires"] > time.time()
            ]
            if (
                len(active) >= self.global_limit
                or sum(slot["tenant"] == scope.tenant_id for slot in active) >= self.tenant_limit
                or sum(slot["run"] == scope.run_id for slot in active) >= self.run_limit
            ):
                return False
            run = self.get(scope.tenant_id, scope.run_id)
            self.repository.put(
                scope.tenant_id,
                "capacity",
                digest(vars(scope)),
                {
                    "id": digest(vars(scope)),
                    "tenant": scope.tenant_id,
                    "run": scope.run_id,
                    "owner": run["lease_owner"],
                    "expires": run["lease_until"],
                },
            )
            return True

    def release_slot(self, scope: Scope) -> None:
        with self.repository.transaction():
            key = digest(vars(scope))
            slot = self.repository.get(scope.tenant_id, "capacity", key)
            owner = self.owners.get((scope.tenant_id, scope.run_id))
            if slot and slot["owner"] == owner:
                slot["expires"] = 0
                self.repository.put(scope.tenant_id, "capacity", key, slot)

    def register(self, name: str, composition: Any, *, version: str = "1.0.0") -> None:
        segment(name)
        describe(composition)
        validate_composition(composition)
        self._validate_policy(composition)
        pending = agents_in(composition)
        while pending:
            agent = pending.pop()
            if agent.name in self.registry and self.registry[agent.name] != agent:
                raise Conflict(f"Conflicting definition for agent {agent.name}")
            self.registry[agent.name] = agent
            pending.extend(child.definition for child in agent.subagents)
        self.workflows[name] = (version, composition)

    def _validate_policy(self, node: Any, inherited: Approval | None = None) -> None:
        if isinstance(node, Step):
            self.policy.resolve(node.approval or node.agent.approval, inherited)
            if node.agent.interrupt_on and not self.policy.allow_native_interrupts:
                raise Forbidden("Native HITL is prohibited by this deployment")
        elif isinstance(node, (Sequence, Parallel)):
            for child in node.children:
                self._validate_policy(child, node.approval or inherited)
        elif isinstance(node, Condition):
            self._validate_policy(node.when_true, inherited)
            self._validate_policy(node.when_false, inherited)
        elif isinstance(node, Loop):
            self._validate_policy(node.body, inherited)
        elif isinstance(node, Supervisor):
            self.policy.resolve(node.approval, inherited)

    def submit(
        self,
        tenant: str,
        task: str,
        workflow: str,
        inputs: Any,
        *,
        idempotency_key: str,
        actor: str = "service",
        purpose: str = "business",
    ) -> dict[str, Any]:
        segment(tenant)
        segment(task)
        if workflow not in self.workflows:
            raise ValueError(f"Unknown workflow {workflow}")
        version, composition = self.workflows[workflow]
        model_snapshots = self._models(composition)
        fingerprint = digest([workflow, version, inputs, task])
        key = digest([task, idempotency_key])
        with self.repository.transaction():
            existing = self.repository.get(tenant, "submission", key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("Idempotency key reused with a different request")
                return self.get(tenant, existing["run_id"])
            run = {
                "id": identity(),
                "tenant_id": tenant,
                "task_id": task,
                "workflow": workflow,
                "version": version,
                "inputs": inputs,
                "definition_hash": digest(describe(composition)),
                "policy": self.policy.snapshot(),
                "capabilities": describe(composition),
                "status": "queued",
                "model_snapshots": model_snapshots,
                "purpose": purpose,
                "created": time.time(),
                "actor": actor,
                "cancel_requested": False,
                "lease_owner": None,
                "lease_until": 0,
                "invocation_count": 0,
            }
            self.repository.put(tenant, "run", run["id"], run)
            self.repository.put(
                tenant, "submission", key, {"fingerprint": fingerprint, "run_id": run["id"]}
            )
            self.repository.event(tenant, run["id"], "run.queued", {"workflow": workflow})
            return run

    def _models(self, composition: Any) -> dict[str, Any]:
        if not self.engine or not hasattr(self.engine, "snapshot"):
            return {}
        result = {}
        pending = agents_in(composition)
        visited = set()
        while pending:
            definition = pending.pop()
            if definition.name in visited:
                continue
            visited.add(definition.name)
            if definition.handler is None:
                result[definition.name] = self.engine.snapshot(definition)
            pending.extend(child.definition for child in definition.subagents)
            for name in definition.delegates:
                if name not in self.registry:
                    raise ValueError(f"Unregistered delegate {name}")
                pending.append(self.registry[name])
        return result

    def get(self, tenant: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get(tenant, "run", run_id)
        if not run:
            raise Forbidden("Run not accessible")
        return run

    def check_cancel(self, scope: Scope) -> None:
        run = self.get(scope.tenant_id, scope.run_id)
        if run["cancel_requested"]:
            raise Cancelled("Run cancelled")
        if run.get("tokens_used", 0) > self.max_tokens:
            raise Conflict("Run token budget exhausted")
        owner = self.owners.get((scope.tenant_id, scope.run_id))
        if owner and (run["lease_owner"] != owner or run["lease_until"] <= time.time()):
            raise Uncertain("Worker lease lost; no further actions are authorized")

    def cancel(self, tenant: str, run_id: str, actor: str) -> None:
        with self.repository.transaction():
            run = self.get(tenant, run_id)
            if run["status"] in ("succeeded", "failed", "cancelled"):
                return
            run["cancel_requested"] = True
            if run["status"] != "running":
                run["status"] = "cancelled"
            self.repository.put(tenant, "run", run_id, run)
            self.repository.event(tenant, run_id, "run.cancel_requested", {"actor": actor})
        active = self.active.get((tenant, run_id))
        if active:
            active.cancel()

    async def _heartbeat(self, tenant: str, run_id: str, owner: str) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            with self.repository.transaction():
                run = self.get(tenant, run_id)
                if run["lease_owner"] != owner:
                    active = self.active.get((tenant, run_id))
                    if active:
                        active.cancel()
                    raise Conflict("Run lease lost")
                if run["cancel_requested"]:
                    active = self.active.get((tenant, run_id))
                    if active:
                        active.cancel()
                run["lease_until"] = time.time() + self.lease_seconds
                self.repository.put(tenant, "run", run_id, run)
                for slot in self.repository.scan(tenant, "capacity"):
                    if slot["run"] == run_id and slot["owner"] == owner and slot["expires"] > 0:
                        slot["expires"] = run["lease_until"]
                        self.repository.put(tenant, "capacity", slot["id"], slot)

    async def execute(self, tenant: str, run_id: str) -> dict[str, Any]:
        if (tenant, run_id) in self.active:
            raise Conflict("Run is already active in this runtime")
        owner = identity()
        with self.repository.transaction():
            run = self.get(tenant, run_id)
            if run["status"] in ("succeeded", "failed", "cancelled"):
                return run
            if run["lease_until"] > time.time():
                raise Conflict("Run already has an active worker")
            if run["workflow"] not in self.workflows:
                raise Conflict("Workflow is not loaded on this worker")
            version, composition = self.workflows[run["workflow"]]
            if version != run["version"] or digest(describe(composition)) != run["definition_hash"]:
                raise Conflict("Workflow changed; resume requires the original definition")
            if run["policy"] != self.policy.snapshot():
                raise Conflict("Deployment policy changed; resume requires the original policy")
            if run["model_snapshots"] != self._models(composition):
                raise Conflict("Model configuration changed; resume requires the original profiles")
            run.update(
                status="running", lease_owner=owner, lease_until=time.time() + self.lease_seconds
            )
            self.repository.put(tenant, "run", run_id, run)
            self.repository.event(tenant, run_id, "run.started", {"worker": owner})
        scope = Scope(tenant, run["task_id"], run_id)
        heartbeat = asyncio.create_task(self._heartbeat(tenant, run_id, owner))
        self.active[(tenant, run_id)] = asyncio.current_task()
        self.owners[(tenant, run_id)] = owner
        result: dict[str, Any] = {}
        try:
            result["output"] = await self._walk(composition, scope, "root", run["inputs"], {}, ())
            self.check_cancel(scope)
            status = "succeeded"
        except Waiting:
            status = "waiting"
        except Uncertain as exc:
            status, result = "uncertain", {"error": str(exc)}
        except (Cancelled, asyncio.CancelledError):
            status = "cancelled" if self.get(tenant, run_id)["cancel_requested"] else "uncertain"
        except Exception as exc:
            status, result = "failed", {"error_type": type(exc).__name__, "error": str(exc)}
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Conflict):
                await heartbeat
            self.active.pop((tenant, run_id), None)
            self.owners.pop((tenant, run_id), None)
        with self.repository.transaction():
            run = self.get(tenant, run_id)
            if run["lease_owner"] != owner:
                raise Conflict("Worker cannot finalize a run after losing its lease")
            run.update(status=status, lease_owner=None, lease_until=0, **result)
            run["has_uncertain_effects"] = any(
                record["run_id"] == run_id and record["status"] in ("running", "uncertain")
                for record in self.repository.scan(tenant, "invocation")
            )
            self.repository.put(tenant, "run", run_id, run)
            self.repository.event(
                tenant,
                run_id,
                f"run.{status}",
                {key: value for key, value in result.items() if key != "output"},
            )
        return run

    async def _walk(
        self,
        node: Any,
        scope: Scope,
        path: str,
        inputs: Any,
        outputs: dict[str, Any],
        stack: tuple[str, ...],
        inherited: Approval | None = None,
    ) -> Any:
        self.check_cancel(scope)
        if isinstance(node, Step):
            result = await self._step(
                node, scope, f"{path}/{node.id}", inputs, outputs, stack, inherited=inherited
            )
            outputs[node.id] = result
            return result
        if isinstance(node, Sequence):
            result = inputs
            for index, child in enumerate(node.children):
                result = await self._walk(
                    child,
                    scope,
                    f"{path}/{index}",
                    inputs,
                    outputs,
                    stack,
                    node.approval or inherited,
                )
            return result
        if isinstance(node, Parallel):
            semaphore = asyncio.Semaphore(node.max_concurrency)

            async def branch(index: int, child: Any) -> tuple[Any, dict[str, Any]]:
                async with semaphore:
                    local = dict(outputs)
                    value = await self._walk(
                        child,
                        scope,
                        f"{path}/{index}",
                        inputs,
                        local,
                        stack,
                        node.approval or inherited,
                    )
                    return value, {
                        key: value
                        for key, value in local.items()
                        if key not in outputs or outputs[key] != value
                    }

            results = await asyncio.gather(
                *(branch(index, child) for index, child in enumerate(node.children)),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            for failure in failures:
                if isinstance(failure, (Waiting, Cancelled, Uncertain, asyncio.CancelledError)):
                    raise failure
            if failures and node.failure_policy == "require_all":
                raise failures[0]
            values, errors = [], []
            merged: dict[str, Any] = {}
            for index, result in enumerate(results):
                if isinstance(result, BaseException):
                    errors.append(
                        {"branch": index, "error_type": type(result).__name__, "error": str(result)}
                    )
                else:
                    value, branch_outputs = result
                    if merged.keys() & branch_outputs.keys():
                        raise Conflict("Parallel branches have conflicting output IDs")
                    merged.update(branch_outputs)
                    values.append(value)
            outputs.update(merged)
            return {"results": values, "errors": errors}
        if isinstance(node, Condition):
            key = digest([scope.run_id, path, node.id, "condition"])
            saved = self.repository.get(scope.tenant_id, "decision", key)
            if saved is None:
                saved = {"choice": bool(node.predicate(inputs, dict(outputs)))}
                self.repository.put(scope.tenant_id, "decision", key, saved)
            chosen = node.when_true if saved["choice"] else node.when_false
            result = await self._walk(
                chosen, scope, f"{path}/{node.id}", inputs, dict(outputs), stack, inherited
            )
            outputs[node.id] = result
            return result
        if isinstance(node, Loop):
            value = inputs
            for index in range(node.max_iterations):
                value = await self._walk(
                    node.body,
                    scope,
                    f"{path}/{node.id}/{index}",
                    value,
                    dict(outputs),
                    stack,
                    inherited,
                )
                key = digest([scope.run_id, path, node.id, index, "until"])
                saved = self.repository.get(scope.tenant_id, "decision", key)
                if saved is None:
                    saved = {"done": bool(node.until(value))}
                    self.repository.put(scope.tenant_id, "decision", key, saved)
                if saved["done"]:
                    outputs[node.id] = value
                    return value
            raise Conflict("Loop iteration budget exhausted")
        if isinstance(node, Supervisor):
            result = await self._supervise(node, scope, path, inputs, outputs, stack)
            outputs[node.id] = result
            return result
        raise TypeError(type(node))

    async def _supervise(
        self,
        node: Supervisor,
        scope: Scope,
        path: str,
        inputs: Any,
        outputs: dict[str, Any],
        stack: tuple[str, ...],
    ) -> Any:
        members = {member.name: member for member in node.members}
        history: list[Any] = []
        coordinator = replace(node.coordinator, output_schema=Decision)
        for index in range(node.max_rounds):
            proposal = await self._step(
                Step(
                    f"{node.id}_decide",
                    coordinator,
                    {
                        "goal": inputs,
                        "history": history,
                        "members": [member.snapshot() for member in members.values()],
                        "memory": Memory(self.repository).search(
                            scope.tenant_id, self.get(scope.tenant_id, scope.run_id)["workflow"]
                        ),
                    },
                ),
                scope,
                f"{path}/{node.id}/decision/{index}",
                inputs,
                outputs,
                stack,
            )
            decision = Decision.model_validate(proposal)
            if decision.action == "finish":
                if decision.calls:
                    raise Conflict("finish cannot include delegated calls")
                return decision.output
            if not decision.calls:
                raise Conflict("delegate requires at least one call")
            proposal = self._approval(
                scope,
                f"{path}/{node.id}/dispatch/{index}",
                node.approval,
                proposal,
                kind="delegation",
                allowed=list(members),
            )
            decision = Decision.model_validate(proposal)
            if decision.action != "delegate" or not decision.calls:
                raise Conflict("Dispatch approval must retain a nonempty delegation")
            for call in decision.calls:
                if call.agent not in members:
                    raise Forbidden("Supervisor selected an agent outside its member allowlist")
            calls = Parallel(
                *(
                    Step(f"call_{call_index}", members[call.agent], call.inputs)
                    for call_index, call in enumerate(decision.calls)
                ),
                max_concurrency=node.max_concurrency,
            )
            value = await self._walk(
                calls,
                scope,
                f"{path}/{node.id}/round/{index}",
                inputs,
                {},
                (*stack, coordinator.name),
            )
            history.append({"decision": proposal, "result": value})
        raise Conflict("Supervisor round budget exhausted")

    def _approval(
        self,
        scope: Scope,
        path: str,
        policy: Approval,
        payload: Any,
        *,
        kind: str,
        allowed: list[str] | None = None,
    ) -> Any:
        if policy.mode == "none" or (policy.condition and not policy.condition(payload)):
            return payload
        approval_id = digest(
            [scope.run_id, path, kind, digest(payload) if kind == "tool" else None]
        )
        with self.repository.transaction():
            record = self.repository.get(scope.tenant_id, "approval", approval_id)
            if record:
                if record["fingerprint"] != digest(payload):
                    raise Conflict("Approval no longer matches the input snapshot")
                if record["expires"] < time.time():
                    raise Conflict("Approval expired; create a new run")
                if record["state"] == "rejected":
                    raise Forbidden("Action rejected by reviewer")
                if record["state"] == "approved":
                    return record["effective"]
            else:
                record = {
                    "id": approval_id,
                    "run_id": scope.run_id,
                    "path": path,
                    "kind": kind,
                    "payload": payload,
                    "fingerprint": digest(payload),
                    "roles": list(policy.roles),
                    "state": "pending",
                    "version": 1,
                    "expires": time.time() + policy.ttl_seconds,
                    "allowed": allowed,
                }
                self.repository.put(scope.tenant_id, "approval", approval_id, record)
                self.repository.event(
                    scope.tenant_id,
                    scope.run_id,
                    "approval.requested",
                    {"id": approval_id, "kind": kind},
                )
        raise Waiting(approval_id)

    def approve(
        self,
        tenant: str,
        approval_id: str,
        *,
        actor: str,
        roles: list[str],
        expected_version: int = 1,
        accept: bool = True,
        edited: Any = None,
    ) -> None:
        with self.repository.transaction():
            record = self.repository.get(tenant, "approval", approval_id)
            if not record or not set(roles) & set(record["roles"]):
                raise Forbidden("Approval is not accessible to this reviewer")
            if record["version"] != expected_version or record["state"] != "pending":
                raise Conflict("Approval was already decided or changed")
            if record["expires"] < time.time():
                raise Conflict("Approval expired")
            effective = record["payload"] if edited is None else edited
            if accept and record["kind"] == "tool":
                effective = prepare_tool_resume(record["payload"], effective)
            if accept and record["kind"] == "delegation":
                decision = Decision.model_validate(effective)
                if decision.action != "delegate" or not decision.calls:
                    raise ValueError("A dispatch approval must delegate")
                for call in decision.calls:
                    if call.agent not in record["allowed"]:
                        raise Forbidden("Replacement agent is not an allowed member")
                    self.registry[call.agent].validate_input(call.inputs)
            if accept and record["kind"] == "step_dispatch":
                if not isinstance(effective, dict) or set(effective) != {"agent", "inputs"}:
                    raise ValueError("Step dispatch requires exactly agent and inputs")
                if effective["agent"] not in record["allowed"]:
                    raise Forbidden("Replacement agent is not an allowed step alternative")
                self.registry[effective["agent"]].validate_input(effective["inputs"])
            record.update(
                state="approved" if accept else "rejected",
                effective=effective,
                actor=actor,
                version=expected_version + 1,
                reviewed=time.time(),
            )
            self.repository.put(tenant, "approval", approval_id, record)
            self.repository.event(
                tenant,
                record["run_id"],
                "approval.decided",
                {
                    "id": approval_id,
                    "state": record["state"],
                    "actor": actor,
                    "edited": edited is not None,
                },
            )
            if accept and record["kind"] in ("delegation", "step_dispatch") and edited is not None:
                memory_candidate = {
                    "original": record["payload"],
                    "replacement": effective,
                    "actor": actor,
                    "approval_id": approval_id,
                }
                run = self.get(tenant, record["run_id"])
                Memory(self.repository).propose(
                    tenant,
                    run["workflow"],
                    memory_candidate,
                    source_run=run["id"],
                    key=approval_id,
                    transaction=False,
                )

    async def _step(
        self,
        step: Step,
        parent: Scope,
        path: str,
        inputs: Any,
        outputs: dict[str, Any],
        stack: tuple[str, ...],
        *,
        inherited_messages: list[Any] | None = None,
        inherited: Approval | None = None,
    ) -> Any:
        definition = step.agent
        if len(stack) >= self.max_depth:
            raise Conflict("Invocation depth exhausted")
        invocation_id = digest([parent.run_id, path])
        value = definition.validate_input(deepcopy(bind(step.inputs, inputs, outputs)))
        original_hash = digest([value, inherited_messages])
        record = self.repository.get(parent.tenant_id, "invocation", invocation_id)
        if record:
            if record["original_hash"] != original_hash:
                raise Conflict("Original invocation input changed during resume")
            if record["status"] == "succeeded":
                return record["output"]
        scope = parent.child(invocation_id, "initial")
        policy = self.policy.resolve(step.approval or definition.approval, inherited)
        if record:
            value = deepcopy(record["inputs"])
            definition = self.registry[record["agent"]]
        elif step.alternatives and policy.mode in ("before", "delegation"):
            approved = self._approval(
                scope,
                path,
                policy,
                {"agent": definition.name, "inputs": value},
                kind="step_dispatch",
                allowed=[definition.name, *(candidate.name for candidate in step.alternatives)],
            )
            definition = self.registry[approved["agent"]]
            value = definition.validate_input(approved["inputs"])
        elif policy.mode in ("before", "delegation"):
            value = definition.validate_input(
                self._approval(scope, path, policy, value, kind="before")
            )
        fingerprint = digest([definition.snapshot(), value, inherited_messages])
        if record and record["fingerprint"] != fingerprint:
            raise Conflict("Invocation definition or inputs changed during resume")
        if record and record["status"] == "succeeded":
            return record["output"]
        if record and record["status"] == "failed":
            raise Conflict(f"Previously failed invocation {invocation_id}: {record['error_type']}")
        if record and record["status"] in ("running", "uncertain"):
            raise Uncertain(f"Invocation {invocation_id} requires side-effect reconciliation")
        if record and record["status"] == "output_ready":
            result = record["output"]
        else:
            if record is None:
                with self.repository.transaction():
                    run = self.get(parent.tenant_id, parent.run_id)
                    if run["invocation_count"] >= self.max_invocations:
                        raise Conflict("Run invocation budget exhausted")
                    run["invocation_count"] += 1
                    self.repository.put(parent.tenant_id, "run", parent.run_id, run)
                    record = {
                        "id": invocation_id,
                        "run_id": parent.run_id,
                        "path": path,
                        "parent_id": parent.invocation_id,
                        "agent": definition.name,
                        "fingerprint": fingerprint,
                        "inputs": deepcopy(value),
                        "attempt": 0,
                        "original_hash": original_hash,
                        "status": "pending",
                        "definition": definition.snapshot(),
                    }
                    self.repository.put(parent.tenant_id, "invocation", invocation_id, record)
            resume = None
            if record["status"] == "interrupted":
                resume = self._interrupt_review(scope, path, record["interrupt"])
                record["status"] = "resuming"
            while True:
                if record["status"] != "resuming":
                    record["attempt"] += 1
                    record["attempt_id"] = identity()
                scope = parent.child(invocation_id, record["attempt_id"])
                record["status"] = "running"
                self._save_invocation(scope, record, "invocation.started")
                context = ExecutionContext(
                    self,
                    scope,
                    path,
                    (*stack, definition.name),
                    (
                        *definition.delegates,
                        *(child.name for child in definition.subagents),
                        *([f"{definition.name}_general"] if definition.general_purpose else []),
                    ),
                )
                context.inherited_messages = inherited_messages
                try:
                    self.check_cancel(scope)
                    async with asyncio.timeout(definition.timeout_seconds):
                        await context.acquire()
                        if definition.handler:
                            if hasattr(definition.handler, "ainvoke"):
                                result = definition.handler.ainvoke(value, context, resume)
                            elif inspect.iscoroutinefunction(definition.handler):
                                result = definition.handler(value, context)
                            else:
                                result = await asyncio.to_thread(definition.handler, value, context)
                            if inspect.isawaitable(result):
                                result = await result
                        elif self.engine:
                            result = await self.engine.invoke(definition, value, context, resume)
                        else:
                            raise ValueError("No DeepAgents engine configured")
                    self.check_cancel(scope)
                    result = definition.validate_output(result)
                    break
                except Waiting:
                    saved = self.repository.get(scope.tenant_id, "invocation", scope.invocation_id)
                    if saved["status"] != "interrupted":
                        record["status"] = "uncertain"
                        self._save_invocation(scope, record, "invocation.uncertain")
                        raise Uncertain(
                            "A Python handler paused without a durable graph checkpoint"
                        ) from None
                    raise
                except (asyncio.CancelledError, Cancelled, Uncertain):
                    record["status"] = "uncertain"
                    self._save_invocation(scope, record, "invocation.uncertain")
                    raise
                except Exception as exc:
                    record.update(status="failed", error_type=type(exc).__name__)
                    self._save_invocation(scope, record, "invocation.failed")
                    if record["attempt"] >= definition.retry.attempts or not isinstance(
                        exc, definition.retry.exceptions
                    ):
                        raise
                    await asyncio.sleep(
                        definition.retry.delay_seconds * 2 ** (record["attempt"] - 1)
                    )
                finally:
                    context.release()
            record.update(status="output_ready", output=result)
            self._save_invocation(scope, record, "invocation.output_ready")
        if policy.mode == "after":
            result = definition.validate_output(
                self._approval(scope, path, policy, result, kind="after")
            )
        record.update(status="succeeded", output=result)
        self._save_invocation(scope, record, "invocation.succeeded")
        return result

    def _save_invocation(self, scope: Scope, record: dict[str, Any], event: str) -> None:
        with self.repository.transaction():
            owner = self.owners.get((scope.tenant_id, scope.run_id))
            if owner and self.get(scope.tenant_id, scope.run_id)["lease_owner"] != owner:
                raise Uncertain("Stale worker cannot update an invocation")
            previous = self.repository.get(scope.tenant_id, "invocation", scope.invocation_id) or {}
            if "model_snapshot" in previous:
                record["model_snapshot"] = previous["model_snapshot"]
            self.repository.put(scope.tenant_id, "invocation", scope.invocation_id, record)
            self.repository.event(
                scope.tenant_id,
                scope.run_id,
                event,
                {
                    "invocation_id": scope.invocation_id,
                    "agent": record["agent"],
                    "attempt": record["attempt"],
                    "parent_id": record["parent_id"],
                },
            )

    def interrupt(self, context: ExecutionContext, payload: Any) -> None:
        if not self.policy.allow_native_interrupts:
            raise Forbidden("Graph interrupts are prohibited by deployment policy")
        scope = context.scope
        record = self.repository.get(scope.tenant_id, "invocation", scope.invocation_id)
        record.update(status="interrupted", interrupt=payload)
        self._save_invocation(scope, record, "invocation.interrupted")
        self._interrupt_review(scope, context.path, payload)

    def _interrupt_review(self, scope: Scope, path: str, payload: Any) -> Any:
        delegated = []
        for item in payload.get("interrupts", []):
            dependency = (
                item["value"].get("framework_approval") if isinstance(item["value"], dict) else None
            )
            if dependency:
                delegated.append(dependency)
                record = self.repository.get(scope.tenant_id, "approval", dependency)
                if not record or record["run_id"] != scope.run_id:
                    raise Forbidden("Nested approval belongs to a different run")
                if record["state"] == "pending":
                    raise Waiting(dependency)
                if record["state"] == "rejected":
                    raise Forbidden("Nested delegation was rejected")
        if delegated and len(delegated) == len(payload["interrupts"]):
            return payload
        return self._approval(scope, path, Approval.before(), payload, kind="tool")

    def reconcile(
        self,
        tenant: str,
        invocation_id: str,
        *,
        actor: str,
        output: Any = None,
        retry: bool = False,
    ) -> None:
        with self.repository.transaction():
            record = self.repository.get(tenant, "invocation", invocation_id)
            if not record or record["status"] not in ("running", "uncertain", "failed"):
                raise Conflict("Invocation does not require reconciliation")
            run = self.get(tenant, record["run_id"])
            if run["lease_until"] > time.time():
                raise Conflict("Cannot reconcile while a worker owns the run")
            if retry:
                record["status"] = "pending"
            else:
                record.update(
                    status="output_ready",
                    output=self.registry[record["agent"]].validate_output(output),
                )
            run.update(status="queued", cancel_requested=False)
            self.repository.put(tenant, "invocation", invocation_id, record)
            self.repository.put(tenant, "run", run["id"], run)
            self.repository.event(
                tenant,
                run["id"],
                "invocation.reconciled",
                {"invocation_id": invocation_id, "actor": actor, "retry": retry},
            )
