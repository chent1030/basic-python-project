from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.harness.kernel import Memory
from app.harness.kernel.domain.models import Conflict, Forbidden, digest
from app.projects.cps.domain.models import MemoryReview, TaskUpdate, VersionedCommand
from app.projects.cps.domain.reporting import render_report


class KnowledgeUseCases:
    def report(self, tenant: str, case_id: str) -> tuple[str, bool]:
        case = self.repository.get(tenant, case_id)
        if case.active_job:
            job = self.records.get(tenant, "cps_job", case.active_job)
            if job["agent"] == "report":
                for approval in self.records.scan(tenant, "approval"):
                    if approval["run_id"] == job["run_id"] and approval["state"] == "pending":
                        return render_report(
                            approval["payload"]["report"],
                            confirmed=False,
                            inspection_id=case_id,
                        ), False
        if "report" not in case.results:
            raise Conflict("No report draft or confirmed report is available")
        return render_report(case.results["report"], confirmed=True, inspection_id=case_id), True

    def archive(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        body: VersionedCommand,
    ) -> dict[str, Any]:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "archive", body, actor):
                self._check_version(case, body.expected_version)
                if case.status == "cancelled":
                    raise Conflict("Cancelled inspection cannot publish a report")
                if "report" not in case.confirmations:
                    raise Conflict("Report must be human-confirmed before archiving")
                archive_id = digest([case_id, case.confirmations["report"]])
                if self.records.get(tenant, "cps_archive", archive_id) is None:
                    record = {
                        "id": archive_id,
                        "inspection_id": case_id,
                        "line_info": case.line_info.model_dump(),
                        "results": case.results,
                        "confirmations": case.confirmations,
                        "evidence": case.evidence,
                        "archived_by": actor,
                        "archived_at": time.time(),
                        "html": render_report(
                            case.results["report"], confirmed=True, inspection_id=case_id
                        ),
                    }
                    self.records.put(tenant, "cps_archive", archive_id, record)
                case.archived_report = archive_id
                self._remember(tenant, case_id, "archive", body, actor)
                self._save(tenant, case, "report_archived", actor, archive_id=archive_id)
                self._enqueue(tenant, case, "observation")
                self.repository.save(tenant, case)
        self.flush(tenant)
        return self.records.get(tenant, "cps_archive", case.archived_report)

    def publish_plan(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        body: VersionedCommand,
    ) -> dict[str, Any]:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "publish_plan", body, actor):
                self._check_version(case, body.expected_version)
                if case.status == "cancelled":
                    raise Conflict("Cancelled inspection cannot publish a work plan")
                if "work_plan" not in case.confirmations:
                    raise Conflict("Work plan must be human-confirmed before publication")
                plan_id = digest([case_id, case.confirmations["work_plan"]])
                if self.records.get(tenant, "cps_plan", plan_id) is None:
                    if any(
                        task["inspection_id"] == case_id
                        and task["status"] not in ("completed", "cancelled")
                        for task in self.records.scan(tenant, "cps_task")
                    ):
                        raise Conflict(
                            "Close or cancel earlier published tasks before replacing the plan"
                        )
                    tasks = case.results["work_plan"]
                    for task in tasks:
                        if (
                            not task["owner_id"]
                            or not task["due_at"]
                            or not task["acceptance_criteria"]
                        ):
                            raise ValueError(
                                "Published tasks require an owner, due date and acceptance criteria"
                            )
                        due = datetime.fromisoformat(task["due_at"].replace("Z", "+00:00"))
                        if due.tzinfo is None:
                            raise ValueError("Task due dates must contain a timezone")
                    ids = {task["task_id"]: digest([plan_id, task["task_id"]]) for task in tasks}
                    for task in tasks:
                        task_id = ids[task["task_id"]]
                        self.records.put(
                            tenant,
                            "cps_task",
                            task_id,
                            {
                                **task,
                                "id": task_id,
                                "plan_id": plan_id,
                                "inspection_id": case_id,
                                "dependencies": [
                                    ids[dependency] for dependency in task["dependencies"]
                                ],
                                "draft": False,
                                "status": "pending",
                                "version": 1,
                                "published_by": actor,
                                "published_at": time.time(),
                                "updates": [],
                            },
                        )
                    self.records.put(
                        tenant,
                        "cps_plan",
                        plan_id,
                        {
                            "id": plan_id,
                            "inspection_id": case_id,
                            "task_ids": list(ids.values()),
                            "published_by": actor,
                            "published_at": time.time(),
                        },
                    )
                case.published_plan = plan_id
                self._remember(tenant, case_id, "publish_plan", body, actor)
                self._save(tenant, case, "plan_published", actor, plan_id=plan_id)
        return self.records.get(tenant, "cps_plan", case.published_plan)

    def update_task(
        self, tenant: str, task_id: str, actor: str, body: TaskUpdate
    ) -> dict[str, Any]:
        with self.records.transaction():
            task = self.records.get(tenant, "cps_task", task_id)
            if not task:
                raise Forbidden("Task not found in this tenant")
            if self._replayed(tenant, task_id, "update_task", body, actor):
                return task
            if task["version"] != body.expected_version:
                raise Conflict("Task version changed")
            if task["status"] in ("completed", "cancelled"):
                raise Conflict("Task is already terminal")
            if body.status != "cancelled":
                for dependency in task["dependencies"]:
                    prerequisite = self.records.get(tenant, "cps_task", dependency)
                    if not prerequisite or prerequisite["status"] != "completed":
                        raise Conflict("Prerequisite task is not completed")
            task.update(status=body.status, version=task["version"] + 1)
            task["updates"].append(
                {"actor": actor, "note": body.note, "at": time.time(), "status": body.status}
            )
            self.records.put(tenant, "cps_task", task_id, task)
            self._remember(tenant, task_id, "update_task", body, actor)
            self.records.event(
                tenant,
                task["inspection_id"],
                "cps.task_updated",
                {
                    "task_id": task_id,
                    "actor": actor,
                    "status": body.status,
                    "note": body.note,
                },
            )
        return task

    def memories(self, tenant: str, line_id: str) -> list[dict[str, Any]]:
        matches = [
            record
            for record in Memory(self.records).search(tenant, "cps")
            if isinstance(record["content"], dict)
            and record["content"].get("scope") in (line_id, "tenant")
        ]
        return sorted(
            matches,
            key=lambda record: record.get("reviewed_at", record["created"]),
            reverse=True,
        )[: self.config.memory_limit]

    def review_memory(
        self,
        tenant: str,
        memory_id: str,
        actor: str,
        body: MemoryReview,
    ) -> dict[str, Any]:
        with self.records.transaction():
            record = self.records.get(tenant, "memory", memory_id)
            if not record or record["namespace"] != "cps":
                raise Forbidden("CPS memory not found")
            if record["version"] != body.expected_version:
                raise Conflict("Memory version changed")
            original = dict(record)
            if body.scope is not None or body.knowledge is not None:
                if body.action not in ("accept", "reject"):
                    raise ValueError("Memory content edits require an explicit review action")
                record["content"] = dict(record["content"])
                if body.scope is not None:
                    record["content"]["scope"] = body.scope
                if body.knowledge is not None:
                    record["content"]["knowledge"] = body.knowledge
            if body.action == "rollback":
                previous = self.records.get(
                    tenant, "memory_history", f"{memory_id}-{body.rollback_version}"
                )
                if not previous or previous["state"] not in ("accepted", "disabled"):
                    raise Conflict("Rollback must reference a previously reviewed memory version")
                for key in ("content", "state", "expires"):
                    record[key] = previous.get(key)
            elif body.action == "enable":
                if record["state"] != "disabled":
                    raise Conflict("Only previously accepted, disabled memories can be enabled")
                record["state"] = "accepted"
            elif body.action == "disable":
                if record["state"] != "accepted":
                    raise Conflict("Only accepted memories can be disabled")
                record["state"] = "disabled"
            else:
                record["state"] = "accepted" if body.action == "accept" else "rejected"
            if body.expires_at:
                if body.expires_at.tzinfo is None or body.expires_at.timestamp() <= time.time():
                    raise ValueError("Memory expiry must be a future timezone-aware timestamp")
                record["expires"] = body.expires_at.timestamp()
            record.update(
                version=record["version"] + 1,
                reviewer=actor,
                reviewed_at=time.time(),
                review_reason=body.reason,
                rollback_version=body.rollback_version,
            )
            self.records.put(
                tenant, "memory_history", f"{memory_id}-{original['version']}", original
            )
            self.records.put(tenant, "memory", memory_id, record)
            self.records.event(
                tenant,
                record["source_run"],
                "memory.reviewed",
                {
                    "id": memory_id,
                    "actor": actor,
                    "action": body.action,
                    "version": record["version"],
                    "reason": body.reason,
                },
            )
        return record

    def events(self, tenant: str, case_id: str, after: int = 0, limit: int = 200):
        case = self.repository.get(tenant, case_id)
        streams = [case_id]
        for job_id in case.jobs:
            job = self.records.get(tenant, "cps_job", job_id)
            if job["run_id"]:
                streams.append(job["run_id"])
        events = [
            event
            for stream in streams
            for event in self.records.events(tenant, stream, after, limit)
        ]
        return sorted(events, key=lambda event: event["cursor"])[:limit]

    def metrics(self, tenant: str) -> dict[str, Any]:
        groups = {"with_memory": [], "without_memory": []}
        group_decisions = {"with_memory": [], "without_memory": []}
        group_cases = {"with_memory": set(), "without_memory": set()}
        decisions = []
        changed = 0
        result_reviews = 0
        memory_supplied = memory_cited = 0
        per_agent: dict[str, dict[str, int]] = {}
        for case in self.repository.list(tenant):
            used_memory = False
            case_decisions = []
            for job_id in case.jobs:
                job = self.records.get(tenant, "cps_job", job_id)
                if job["agent"] == "main" and job.get("review"):
                    supplied = bool(job["snapshot"]["context"].get("relevant_memories"))
                    used_memory |= supplied
                    memory_supplied += int(supplied)
                    memory_cited += int(
                        supplied
                        and any(
                            source.startswith("memory:")
                            for source in job["review"]["original"]["source_refs"]
                        )
                    )
                    decisions.append(job["review"])
                    case_decisions.append(job["review"])
                elif job.get("review") and job["agent"] != "observation":
                    result_reviews += 1
                    edited = int(job["review"]["original"] != job["review"]["effective"])
                    changed += edited
                    agent_metrics = per_agent.setdefault(
                        job["agent"], {"review_count": 0, "edit_count": 0}
                    )
                    agent_metrics["review_count"] += 1
                    agent_metrics["edit_count"] += edited
            group = "with_memory" if used_memory else "without_memory"
            group_cases[group].add(case.id)
            group_decisions[group].extend(case_decisions)
            if case.status == "completed":
                groups[group].append((case.completed_at or case.updated_at) - case.created_at)
        memory = [
            record for record in self.records.scan(tenant, "memory") if record["namespace"] == "cps"
        ]
        return {
            "dispatch_count": len(decisions),
            "redispatch_rate": sum(item["action"] == "modify" for item in decisions)
            / len(decisions)
            if decisions
            else None,
            "direct_approval_rate": sum(item["action"] == "approve" for item in decisions)
            / len(decisions)
            if decisions
            else None,
            "result_edit_rate": changed / result_reviews if result_reviews else None,
            "agent_reviews": per_agent,
            "memory_hit_rate": memory_cited / memory_supplied if memory_supplied else None,
            "memory_acceptance_rate": sum(item["state"] == "accepted" for item in memory)
            / len(memory)
            if memory
            else None,
            "completion_seconds": {
                group: {
                    "sample_count": len(values),
                    "mean": sum(values) / len(values) if values else None,
                }
                for group, values in groups.items()
            },
            "history": self.statistics(tenant),
            "comparison": {
                group: {
                    "case_count": len(group_cases[group]),
                    "dispatch_count": len(group_decisions[group]),
                    "redispatch_rate": sum(
                        decision["action"] == "modify" for decision in group_decisions[group]
                    )
                    / len(group_decisions[group])
                    if group_decisions[group]
                    else None,
                    "history": self.statistics(tenant, group_cases[group]),
                }
                for group in groups
            },
            "limitations": [
                "记忆组与非记忆组是观察性统计，不是因果实验；样本不足时不声称优化有效。",
                "分组表示审核时向主 Agent 提供过有效记忆，"
                "命中表示建议显式引用记忆，不推断隐含采纳。",
            ],
        }
