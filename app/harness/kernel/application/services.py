from __future__ import annotations

import base64
import hashlib
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ..domain.models import Conflict, Forbidden, Scope, digest, identity
from ..domain.ports import Repository


class Workspace:
    def __init__(self, root: Path, scope: Scope):
        self.root = root.resolve().joinpath(*vars(scope).values())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, relative: str) -> Path:
        candidate = self.root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise Forbidden("Workspace path traversal")
        if any(part.is_symlink() for part in [candidate, *candidate.parents]):
            raise Forbidden("Workspace symlinks are not permitted")
        if not candidate.resolve().is_relative_to(self.root):
            raise Forbidden("Workspace escape")
        return candidate

    def write(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read(self, relative: str) -> str:
        return self.path(relative).read_text(encoding="utf-8")


class Artifacts:
    def __init__(self, repository: Repository, scope: Scope, max_bytes: int = 10_000_000):
        self.repository, self.scope, self.max_bytes = repository, scope, max_bytes

    def publish(
        self,
        name: str,
        content: bytes | str,
        *,
        expected_version: int = 0,
        readers: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        data = content.encode() if isinstance(content, str) else content
        if len(data) > self.max_bytes:
            raise ValueError("Artifact size budget exceeded")
        scope = self.scope
        stream = digest([scope.run_id, scope.invocation_id, name])
        with self.repository.transaction():
            head = self.repository.get(scope.tenant_id, "artifact_head", stream) or {"version": 0}
            if head["version"] != expected_version:
                raise Conflict("Artifact version conflict")
            record = {
                "id": identity(),
                "name": name,
                "run_id": scope.run_id,
                "task_id": scope.task_id,
                "owner": scope.invocation_id,
                "version": expected_version + 1,
                "readers": list(readers),
                "granted_runs": [],
                "content": base64.b64encode(data).decode(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "created": time.time(),
            }
            self.repository.put(scope.tenant_id, "artifact", record["id"], record)
            self.repository.put(
                scope.tenant_id,
                "artifact_head",
                stream,
                {"id": record["id"], "version": record["version"]},
            )
            self.repository.event(
                scope.tenant_id,
                scope.run_id,
                "artifact.published",
                {key: value for key, value in record.items() if key != "content"},
            )
        return {key: value for key, value in record.items() if key != "content"}

    def read(self, artifact_id: str) -> bytes:
        record = self.repository.get(self.scope.tenant_id, "artifact", artifact_id)
        if not record:
            raise Forbidden("Artifact not accessible")
        same_run = record["run_id"] == self.scope.run_id
        permitted = (
            self.scope.invocation_id == record["owner"]
            or self.scope.invocation_id in record["readers"]
            or "*" in record["readers"]
        )
        if not ((same_run and permitted) or self.scope.run_id in record["granted_runs"]):
            raise Forbidden("Artifact requires an explicit sharing grant")
        return base64.b64decode(record["content"])

    def grant(self, artifact_id: str, target_run: str) -> None:
        with self.repository.transaction():
            record = self.repository.get(self.scope.tenant_id, "artifact", artifact_id)
            target = self.repository.get(self.scope.tenant_id, "run", target_run)
            if (
                not record
                or not target
                or record["owner"] != self.scope.invocation_id
                or record["run_id"] != self.scope.run_id
            ):
                raise Forbidden("Only the publishing invocation can grant reuse")
            record["granted_runs"] = sorted(set([*record["granted_runs"], target_run]))
            self.repository.put(self.scope.tenant_id, "artifact", artifact_id, record)
            self.repository.event(
                self.scope.tenant_id,
                self.scope.run_id,
                "artifact.granted",
                {"artifact_id": artifact_id, "target_run": target_run},
            )


class Mailbox:
    def __init__(self, repository: Repository, scope: Scope):
        self.repository, self.scope = repository, scope

    def send(self, recipient: str, payload: Any, *, key: str, ttl_seconds: int = 3600) -> str:
        scope = self.scope
        message_id = digest([scope.run_id, scope.invocation_id, key])
        record = {
            "id": message_id,
            "run_id": scope.run_id,
            "sender": scope.invocation_id,
            "recipient": recipient,
            "payload": payload,
            "state": "pending",
            "expires": time.time() + ttl_seconds,
        }
        with self.repository.transaction():
            old = self.repository.get(scope.tenant_id, "message", message_id)
            if old:
                if old["recipient"] != recipient or old["payload"] != payload:
                    raise Conflict("Message idempotency key reused with different contents")
                return message_id
            self.repository.put(scope.tenant_id, "message", message_id, record)
            self.repository.event(
                scope.tenant_id,
                scope.run_id,
                "message.sent",
                {"id": message_id, "recipient": recipient},
            )
        return message_id

    def receive(self) -> list[dict[str, Any]]:
        return [
            message
            for message in self.repository.scan(self.scope.tenant_id, "message")
            if message["run_id"] == self.scope.run_id
            and message["recipient"] == self.scope.invocation_id
            and message["state"] == "pending"
            and message["expires"] > time.time()
        ]

    def acknowledge(self, message_id: str) -> None:
        with self.repository.transaction():
            message = self.repository.get(self.scope.tenant_id, "message", message_id)
            if (
                not message
                or message["recipient"] != self.scope.invocation_id
                or message["run_id"] != self.scope.run_id
            ):
                raise Forbidden("Message not accessible")
            if message["state"] == "consumed":
                return
            message["state"] = "consumed"
            self.repository.put(self.scope.tenant_id, "message", message_id, message)
            self.repository.event(
                self.scope.tenant_id, self.scope.run_id, "message.consumed", {"id": message_id}
            )


class Memory:
    def __init__(self, repository: Repository):
        self.repository = repository

    def propose(
        self,
        tenant: str,
        namespace: str,
        content: Any,
        *,
        source_run: str,
        key: str,
        evidence: list[int] | None = None,
        transaction: bool = True,
    ) -> str:
        candidate_id = digest([namespace, key])
        with self.repository.transaction() if transaction else nullcontext():
            existing = self.repository.get(tenant, "memory", candidate_id)
            if existing:
                if existing["content"] != content:
                    raise Conflict("Memory proposal key collision")
                return candidate_id
            record = {
                "id": candidate_id,
                "namespace": namespace,
                "content": content,
                "source_run": source_run,
                "state": "proposed",
                "version": 1,
                "evidence": evidence or [],
                "created": time.time(),
            }
            self.repository.put(tenant, "memory", candidate_id, record)
            self.repository.event(tenant, source_run, "memory.proposed", {"id": candidate_id})
        return candidate_id

    def review(
        self,
        tenant: str,
        candidate_id: str,
        *,
        actor: str,
        roles: list[str],
        accept: bool,
        expected_version: int,
        expires: float | None = None,
    ) -> None:
        if "memory_reviewer" not in roles:
            raise Forbidden("Memory review requires memory_reviewer")
        with self.repository.transaction():
            record = self.repository.get(tenant, "memory", candidate_id)
            if not record or record["version"] != expected_version:
                raise Conflict("Memory review version conflict")
            previous = dict(record)
            record.update(
                state="accepted" if accept else "rejected",
                reviewer=actor,
                version=expected_version + 1,
                expires=expires,
            )
            self.repository.put(
                tenant, "memory_history", f"{candidate_id}-{expected_version}", previous
            )
            self.repository.put(tenant, "memory", candidate_id, record)
            self.repository.event(
                tenant,
                record["source_run"],
                "memory.reviewed",
                {"id": candidate_id, "actor": actor, "state": record["state"]},
            )

    def search(self, tenant: str, namespace: str, query: str = "") -> list[dict[str, Any]]:
        return [
            record
            for record in self.repository.scan(tenant, "memory")
            if record["namespace"] == namespace
            and record["state"] == "accepted"
            and (not record.get("expires") or record["expires"] > time.time())
            and query.casefold() in str(record["content"]).casefold()
        ]


class Observer:
    def __init__(
        self,
        repository: Repository,
        name: str,
        handler: Any,
        *,
        include_observer_runs: bool = False,
    ):
        self.repository, self.name, self.handler = repository, name, handler
        self.include_observer_runs = include_observer_runs

    async def drain(self, tenant: str, run_id: str) -> int:
        key = digest([self.name, run_id])
        checkpoint = self.repository.get(tenant, "observer", key) or {"cursor": 0}
        events = self.repository.events(tenant, run_id, checkpoint["cursor"])
        for event in events:
            if not event["kind"].startswith(("observer.", "memory.")):
                try:
                    await self.handler(event, Memory(self.repository))
                except Exception as exc:
                    self.repository.event(
                        tenant,
                        run_id,
                        "observer.failed",
                        {"name": self.name, "error_type": type(exc).__name__},
                    )
                    return checkpoint["cursor"]
            checkpoint["cursor"] = event["cursor"]
            self.repository.put(tenant, "observer", key, checkpoint)
        return checkpoint["cursor"]
