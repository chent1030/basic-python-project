from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

from app.harness.kernel import Runtime
from app.harness.kernel.domain.models import Conflict, Forbidden, FrameworkError, digest, identity
from app.projects.cps.domain.contracts import CONTRACTS
from app.projects.cps.domain.models import (
    SPECIALISTS,
    AgentInput,
    AmendInspection,
    CreateInspection,
    EvidenceInput,
    Inspection,
    StopInspection,
    VersionedCommand,
)
from app.projects.cps.domain.ports import ImageEncoder, ImageValidator, InspectionRepository
from app.projects.cps.domain.reporting import calculate_history, cosine

from .knowledge import KnowledgeUseCases
from .reviews import ReviewUseCases


class InspectionService(ReviewUseCases, KnowledgeUseCases):
    def __init__(
        self,
        runtime: Runtime,
        repository: InspectionRepository,
        config: Any,
        validator: ImageValidator,
        encoder: ImageEncoder | None = None,
    ):
        self.runtime, self.repository, self.config = runtime, repository, config
        self.records = runtime.repository
        self.encoder = encoder
        self.validator = validator

    def _check_version(self, case: Inspection, expected: int) -> None:
        if case.version != expected:
            raise Conflict("Inspection changed; reload its current version")

    def _idle(self, case: Inspection) -> None:
        if case.active_job or case.status in ("completed", "cancelled"):
            raise Conflict("Inspection is active or closed; return it for collection first")

    def _save(self, tenant: str, case: Inspection, kind: str, actor: str, **data: Any) -> None:
        case.version += 1
        case.updated_at = time.time()
        self.repository.save(tenant, case)
        self.records.event(
            tenant,
            case.id,
            f"cps.{kind}",
            {"inspection_id": case.id, "actor": actor, "version": case.version, **data},
        )

    def _replayed(self, tenant: str, case_id: str, operation: str, body: Any, actor: str) -> bool:
        key = digest([case_id, operation, body.idempotency_key])
        command = self.records.get(tenant, "cps_command", key)
        if command and command["fingerprint"] != digest([body.model_dump(mode="json"), actor]):
            raise Conflict("Idempotency key reused with a different command or actor")
        return command is not None

    def _remember(self, tenant: str, case_id: str, operation: str, body: Any, actor: str) -> None:
        key = digest([case_id, operation, body.idempotency_key])
        self.records.put(
            tenant,
            "cps_command",
            key,
            {
                "fingerprint": digest([body.model_dump(mode="json"), actor]),
            },
        )

    def create(self, tenant: str, actor: str, body: CreateInspection) -> Inspection:
        case_id = digest([actor, body.idempotency_key])
        with self.records.transaction():
            if self._replayed(tenant, case_id, "create", body, actor):
                return self.repository.get(tenant, case_id)
            now = time.time()
            case = Inspection(
                id=case_id,
                owner=actor,
                goal=body.goal,
                line_info=body.line_info,
                required_outputs=body.required_outputs,
                created_at=now,
                updated_at=now,
            )
            self.repository.save(tenant, case)
            self._remember(tenant, case_id, "create", body, actor)
            self.records.event(tenant, case_id, "cps.created", {"actor": actor})
        return case

    def amend(self, tenant: str, case_id: str, actor: str, body: AmendInspection) -> Inspection:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            self._check_version(case, body.expected_version)
            self._idle(case)
            if body.goal is not None:
                case.goal = body.goal
            if body.line_info is not None:
                case.line_info = body.line_info
            case.notes.append({"actor": actor, "note": body.note, "at": time.time()})
            case.invalidate()
            case.status = "collecting"
            self._save(tenant, case, "amended", actor)
        return case

    async def add_evidence(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        body: EvidenceInput,
    ) -> dict[str, Any]:
        case = self.repository.get(tenant, case_id)
        evidence_id = digest([case_id, actor, body.idempotency_key])
        if self._replayed(tenant, case_id, "evidence", body, actor):
            return next(item for item in case.evidence if item["id"] == evidence_id)
        self._check_version(case, body.expected_version)
        self._idle(case)
        if len(case.evidence) >= self.config.max_images:
            raise Conflict("Inspection evidence limit reached")
        if body.kind == "after" and (
            not body.issue_id
            or body.issue_id not in {issue["issue_id"] for issue in case.results.get("issues", [])}
        ):
            raise ValueError("After evidence must reference a confirmed issue")
        content, mime, width, height = self.validator(
            body.content_base64, self.config.max_image_bytes
        )
        encoded = base64.b64encode(content).decode()
        data_url = f"data:{mime};base64,{encoded}"
        vector = await self.encoder.encode(data_url) if self.encoder else None
        metadata = {
            "id": evidence_id,
            "kind": body.kind,
            "issue_id": body.issue_id,
            "mime": mime,
            "width": width,
            "height": height,
            "bytes": len(content),
            "note": body.note,
            "sha256": hashlib.sha256(content).hexdigest(),
            "issue_confirmation": digest(case.confirmations.get("issues"))
            if body.kind == "after"
            else None,
            "created_at": time.time(),
        }
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if self._replayed(tenant, case_id, "evidence", body, actor):
                return next(item for item in case.evidence if item["id"] == evidence_id)
            self._check_version(case, body.expected_version)
            self._idle(case)
            self.records.put(
                tenant,
                "cps_evidence",
                evidence_id,
                {
                    **metadata,
                    "inspection_id": case_id,
                    "base64": encoded,
                    "vector": vector,
                    "embedding_model": self.config.embeddings.model,
                },
            )
            if body.kind == "after":
                for key in ("rectification", "report", "work_plan"):
                    case.results.pop(key, None)
                    case.confirmations.pop(key, None)
                case.archived_report = case.published_plan = None
                case.evidence_version += 1
            else:
                case.invalidate()
            case.evidence.append(metadata)
            case.status = "collecting"
            self._remember(tenant, case_id, "evidence", body, actor)
            self._save(tenant, case, "evidence_added", actor, evidence=metadata)
        return metadata

    def evidence(self, tenant: str, case_id: str, evidence_id: str) -> dict[str, Any]:
        self.repository.get(tenant, case_id)
        record = self.records.get(tenant, "cps_evidence", evidence_id)
        if not record or record["inspection_id"] != case_id:
            raise Forbidden("Evidence is not part of this inspection")
        return record

    def statistics(self, tenant: str, case_ids: set[str] | None = None) -> dict[str, Any]:
        latest = {}
        for record in self.records.scan(tenant, "cps_archive"):
            if case_ids is not None and record["inspection_id"] not in case_ids:
                continue
            previous = latest.get(record["inspection_id"])
            if previous is None or record["archived_at"] > previous["archived_at"]:
                latest[record["inspection_id"]] = record
        now = time.time()
        return calculate_history(list(latest.values()), now - self.config.history_days * 86400, now)

    def similar(self, tenant: str, case: Inspection, limit: int = 5) -> list[dict[str, Any]]:
        queries = [
            self.evidence(tenant, case.id, item["id"]).get("vector") for item in case.evidence
        ]
        queries = [query for query in queries if query]
        if not queries:
            return []
        matches = []
        for record in self.records.scan(tenant, "cps_archive"):
            if record["inspection_id"] == case.id:
                continue
            for evidence in record["evidence"]:
                saved = self.records.get(tenant, "cps_evidence", evidence["id"])
                if not saved or not saved.get("vector"):
                    continue
                if saved.get("embedding_model") != self.config.embeddings.model:
                    continue
                matches.append(
                    {
                        "source_ref": f"archive:{record['id']}",
                        "evidence_id": evidence["id"],
                        "score": max(cosine(query, saved["vector"]) for query in queries),
                        "confirmed_issues": record["results"].get("issues", []),
                    }
                )
        return sorted(matches, key=lambda item: item["score"], reverse=True)[:limit]

    def _snapshot(self, tenant: str, case: Inspection, instruction: str) -> dict[str, Any]:
        statistics = self.statistics(tenant)
        memories = self.memories(tenant, case.line_info.line_id)
        similar_cases = self.similar(tenant, case)
        sources = [
            "inspection",
            "statistics",
            "line_info",
            "history",
            "coverage_baseline",
            *(f"evidence:{item['id']}" for item in case.evidence),
            *(f"confirmed:{key}" for key in case.confirmations),
            *(f"memory:{record['id']}" for record in memories),
            *statistics["source_refs"],
            *(match["source_ref"] for match in similar_cases),
        ]
        return {
            "inspection_id": case.id,
            "goal": case.goal,
            "line_info": case.line_info.model_dump(),
            "instruction": instruction,
            "available_agents": list(SPECIALISTS),
            "sources": sources,
            "context": {
                "evidence": case.evidence,
                **case.results,
                "confirmations": case.confirmations,
                "history": case.history,
                "notes": case.notes,
                "relevant_memories": memories,
                "statistics": statistics,
                "similar_cases": similar_cases,
                "coverage_baseline": case.line_info.expected_scenarios,
                "required_outputs": case.required_outputs,
                "archived_report": case.archived_report,
                "published_plan": case.published_plan,
                "can_finish": case.can_finish(),
                "retrieval_limitations": []
                if self.encoder
                else ["未配置图片向量服务；未执行相似检索"],
            },
        }

    def _enqueue(
        self, tenant: str, case: Inspection, agent: str, instruction: str = ""
    ) -> str | None:
        if agent == "main" and case.rounds >= self.config.max_rounds:
            case.status = "needs_human"
            case.active_job = None
            return None
        job_id = identity()
        snapshot = self._snapshot(tenant, case, instruction)
        if agent == "observation":
            events = []
            cursor = 0
            while batch := self.events(tenant, case.id, cursor, 1000):
                events.extend(batch)
                cursor = batch[-1]["cursor"]
            snapshot["context"]["chain_events"] = events[-500:]
            snapshot["context"]["omitted_event_count"] = max(0, len(events) - 500)
            snapshot["sources"].extend(f"event:{event['cursor']}" for event in events[-500:])
        self.records.put(
            tenant,
            "cps_job",
            job_id,
            {
                "id": job_id,
                "inspection_id": case.id,
                "agent": agent,
                "snapshot": snapshot,
                "evidence_version": case.evidence_version,
                "run_id": None,
                "applied": False,
                "review": None,
                "created_at": time.time(),
            },
        )
        case.jobs.append(job_id)
        if agent != "observation":
            case.active_job = job_id
            case.status = "active"
        if agent == "main":
            case.rounds += 1
        return job_id

    def flush(self, tenant: str) -> None:
        for job in self.records.scan(tenant, "cps_job"):
            if job["applied"]:
                continue
            try:
                self._flush_job(tenant, job)
                if job.get("delivery_error"):
                    with self.records.transaction():
                        current = self.records.get(tenant, "cps_job", job["id"])
                        current.pop("delivery_error", None)
                        self.records.put(tenant, "cps_job", job["id"], current)
            except (FrameworkError, ValueError, KeyError) as exc:
                self.record_job_error(tenant, job["id"], "delivery_error", exc)

    def record_job_error(self, tenant: str, job_id: str, field: str, error: Exception) -> None:
        failure = {"error_type": type(error).__name__, "detail": str(error)}
        with self.records.transaction():
            job = self.records.get(tenant, "cps_job", job_id)
            if job.get(field) == failure:
                return
            job[field] = failure
            self.records.put(tenant, "cps_job", job_id, job)
            self.records.event(
                tenant,
                job["inspection_id"],
                f"cps.{field}",
                {
                    "job_id": job_id,
                    **failure,
                },
            )

    def _flush_job(self, tenant: str, job: dict[str, Any]) -> None:
        if job.get("stop") and job["run_id"]:
            self.runtime.cancel(tenant, job["run_id"], job["stop"]["actor"])
            return
        if job.get("review"):
            self._flush_review(tenant, job)
        if job["run_id"]:
            return
        run = self.runtime.submit(
            tenant,
            job["inspection_id"],
            f"cps_{job['agent']}",
            {"inspection_id": job["inspection_id"], "job_id": job["id"]},
            idempotency_key=job["id"],
            actor="cps-service",
            purpose="observer" if job["agent"] == "observation" else "business",
        )
        with self.records.transaction():
            current = self.records.get(tenant, "cps_job", job["id"])
            current["run_id"] = run["id"]
            self.records.put(tenant, "cps_job", job["id"], current)

    def stop(self, tenant: str, case_id: str, actor: str, body: StopInspection) -> Inspection:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "stop", body, actor):
                self._check_version(case, body.expected_version)
                if case.status in ("completed", "cancelled"):
                    raise Conflict("Inspection is already closed")
                if case.active_job:
                    job = self.records.get(tenant, "cps_job", case.active_job)
                    job["stop"] = {"actor": actor, "action": body.action, "reason": body.reason}
                    self.records.put(tenant, "cps_job", job["id"], job)
                else:
                    case.status = "cancelled" if body.action == "cancel" else "needs_input"
                self._remember(tenant, case_id, "stop", body, actor)
                self._save(
                    tenant, case, "stop_requested", actor, reason=body.reason, action=body.action
                )
        self.flush(tenant)
        current = self.repository.get(tenant, case_id)
        if current.active_job:
            job = self.records.get(tenant, "cps_job", current.active_job)
            if job["run_id"]:
                self.runtime.cancel(tenant, job["run_id"], actor)
                self.advance(tenant, job["run_id"])
        return self.repository.get(tenant, case_id)

    def start(self, tenant: str, case_id: str, actor: str, body: VersionedCommand) -> Inspection:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "start", body, actor):
                self._check_version(case, body.expected_version)
                self._idle(case)
                self._enqueue(tenant, case, "main")
                self._remember(tenant, case_id, "start", body, actor)
                self._save(tenant, case, "started", actor)
        self.flush(tenant)
        return self.repository.get(tenant, case_id)

    def require_job(
        self, tenant: str, run_id: str, inputs: Any
    ) -> tuple[Inspection, dict[str, Any]]:
        parsed = AgentInput.model_validate(inputs)
        case = self.repository.get(tenant, parsed.inspection_id)
        job = self.records.get(tenant, "cps_job", parsed.job_id)
        submission = self.records.get(tenant, "submission", digest([case.id, parsed.job_id]))
        if not job or job["inspection_id"] != case.id or not submission:
            raise Forbidden("CPS execution requires a durable business dispatch")
        if submission["run_id"] != run_id:
            raise Forbidden("Run is not the one authorized by the business dispatch")
        if job["agent"] != "observation" and case.active_job != job["id"]:
            raise Conflict("This CPS job is no longer active")
        if job.get("stop"):
            raise Conflict("CPS job was stopped before execution")
        if job["evidence_version"] != case.evidence_version and job["agent"] != "observation":
            raise Conflict("Evidence changed since this dispatch")
        return case, job

    def validate_result(self, agent: str, output: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
        result = CONTRACTS[agent].model_validate(output).model_dump(mode="json")
        available = set(snapshot["sources"])

        def check_sources(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "source_refs" and any(source not in available for source in item):
                        raise ValueError("Output cites a source outside the authorized snapshot")
                    check_sources(item)
            elif isinstance(value, list):
                for item in value:
                    check_sources(item)

        check_sources(result)
        if agent == "main" and result["agent_name"] not in (None, *SPECIALISTS):
            raise ValueError("Main Agent selected an unavailable specialist")
        if agent == "issue_identification":
            ids = [issue["issue_id"] for issue in result["issues"]]
            if len(set(ids)) != len(ids) or any(not issue_id.strip() for issue_id in ids):
                raise ValueError("Issue identifiers must be nonempty and unique")
        if agent == "rectification_judgement":
            known = {issue["issue_id"] for issue in snapshot["context"].get("issues", [])}
            checks = result["rectification"]["checks"]
            if any(check["issue_id"] not in known for check in checks):
                raise ValueError("Rectification references an unconfirmed issue")
            if result["rectification"]["status"] == "pass":
                if not known or {check["issue_id"] for check in checks} != known:
                    raise ValueError("A passing judgement must cover all confirmed issues")
        if agent == "history_analysis":
            statistics = snapshot["context"]["statistics"]
            for metric in result["history_analysis"]["metrics"]:
                if metric["name"] not in (
                    "record_count",
                    "issue_count",
                    "repeat_occurrences",
                    "recurrence_rate",
                ):
                    raise ValueError(
                        "History metric is not in the deterministic statistics catalog"
                    )
                if metric["value"] != statistics[metric["name"]] or metric["source_refs"] != [
                    "statistics"
                ]:
                    raise ValueError("Model statistics do not match the computed source")
                denominator = (
                    statistics["issue_count"] if metric["name"] == "recurrence_rate" else None
                )
                if metric["denominator"] != denominator:
                    raise ValueError("Incorrect statistics denominator")
                unit = "ratio" if metric["name"] == "recurrence_rate" else "count"
                if metric["unit"] != unit:
                    raise ValueError(
                        "Statistics units must be count or ratio as defined by the catalog"
                    )
        if agent == "work_plan":
            tasks = result["work_plan"]
            ids = {task["task_id"] for task in tasks}
            if len(ids) != len(tasks) or any(not task_id.strip() for task_id in ids):
                raise ValueError("Work task identifiers must be nonempty and unique")
            pending = {task["task_id"]: set(task["dependencies"]) for task in tasks}
            if any(not dependencies <= ids for dependencies in pending.values()):
                raise ValueError("Unknown work task dependency")
            completed: set[str] = set()
            while pending:
                ready = [
                    task_id
                    for task_id, dependencies in pending.items()
                    if dependencies <= completed
                ]
                if not ready:
                    raise ValueError("Work task dependencies contain a cycle")
                for task_id in ready:
                    completed.add(task_id)
                    pending.pop(task_id)
        return result

    def detail(self, tenant: str, case_id: str) -> dict[str, Any]:
        case = self.repository.get(tenant, case_id)
        value = case.model_dump(mode="json")
        value["can_finish"] = case.can_finish()
        value["active_run"] = None
        value["phase"] = case.status
        value["outbox_error"] = None
        value["approvals"] = []
        if case.active_job:
            job = self.records.get(tenant, "cps_job", case.active_job)
            value["outbox_error"] = job.get("delivery_error") or job.get("projection_error")
            if job["run_id"]:
                run = self.runtime.get(tenant, job["run_id"])
                value["active_run"] = {
                    key: run.get(key)
                    for key in (
                        "id",
                        "workflow",
                        "status",
                        "tokens_used",
                        "error_type",
                        "created",
                    )
                }
                value["approvals"] = [
                    record
                    for record in self.records.scan(tenant, "approval")
                    if record["run_id"] == run["id"] and record["state"] == "pending"
                ]
        if value["active_run"]:
            value["phase"] = value["active_run"]["status"]
            if value["approvals"]:
                value["phase"] = (
                    "waiting_dispatch_confirmation"
                    if job["agent"] == "main"
                    else "waiting_result_confirmation"
                )
        if value["outbox_error"]:
            value["phase"] = "needs_operator_recovery"
        return value
