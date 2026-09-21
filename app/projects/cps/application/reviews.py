from __future__ import annotations

import time
from typing import Any

from app.harness.kernel import Memory
from app.harness.kernel.domain.models import Conflict, Forbidden, digest
from app.projects.cps.domain.models import (
    RESULT_KEYS,
    DispatchReview,
    Inspection,
    ManualResult,
    ResultReview,
)


class ReviewUseCases:
    def _pending_job(self, tenant: str, case: Inspection, *, main: bool):
        if not case.active_job:
            raise Conflict("Inspection has no pending execution")
        job = self.records.get(tenant, "cps_job", case.active_job)
        if (job["agent"] == "main") != main or job["review"] is not None:
            raise Conflict("This job is not awaiting this kind of review")
        approvals = [
            record
            for record in self.records.scan(tenant, "approval")
            if record["run_id"] == job["run_id"] and record["state"] == "pending"
        ]
        if len(approvals) != 1 or approvals[0]["kind"] != "after":
            raise Conflict("Agent has not produced a reviewable result")
        return job, approvals[0]

    def _can_dispatch(self, case: Inspection, agent: str) -> None:
        if agent == "issue_identification" and not any(
            item["kind"] == "before" for item in case.evidence
        ):
            raise Conflict("Issue identification requires actual before images")
        if agent == "rectification_judgement":
            known = {issue["issue_id"] for issue in case.results.get("issues", [])}
            after = {
                item["issue_id"]
                for item in case.evidence
                if item["kind"] == "after"
                and item.get("issue_confirmation") == digest(case.confirmations.get("issues"))
            }
            if not known or not known <= after:
                raise Conflict(
                    "Rectification requires confirmed issues and after evidence for each"
                )

    def dispatch(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        roles: list[str],
        body: DispatchReview,
    ) -> dict[str, Any]:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "dispatch", body, actor):
                self._check_version(case, body.expected_version)
                job, approval = self._pending_job(tenant, case, main=True)
                if not set(roles) & set(approval["roles"]):
                    raise Forbidden("CPS dispatch review role is required")
                proposal = self.validate_result("main", approval["payload"], job["snapshot"])
                selected = None
                if body.action in ("approve", "modify", "retry"):
                    selected = body.selected_agent or proposal["agent_name"]
                    if body.action in ("approve", "retry") and body.selected_agent not in (
                        None,
                        proposal["agent_name"],
                    ):
                        raise ValueError("Use modify to replace the recommended agent")
                    if selected:
                        self._can_dispatch(case, selected)
                    elif body.action in ("modify", "retry"):
                        raise ValueError("Retry or modification requires a specialist")
                    if not selected and proposal["action"] == "finish" and not case.can_finish():
                        raise Conflict(
                            "Required conclusions, report archive or plan publication are missing"
                        )
                effective = dict(proposal)
                if selected:
                    effective.update(action="propose_agent", agent_name=selected)
                review = {
                    "action": body.action,
                    "selected_agent": selected,
                    "actor": actor,
                    "roles": roles,
                    "reason": body.reason,
                    "instruction": body.instruction,
                    "original": proposal,
                    "effective": effective,
                    "accept": True,
                    "approval_id": approval["id"],
                    "approval_version": approval["version"],
                    "at": time.time(),
                }
                job["review"] = review
                self.records.put(tenant, "cps_job", job["id"], job)
                self._remember(tenant, case_id, "dispatch", body, actor)
                self._save(tenant, case, "dispatch_reviewed", actor, decision=review)
        self.flush(tenant)
        return self.detail(tenant, case_id)

    def review_result(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        roles: list[str],
        body: ResultReview,
    ) -> dict[str, Any]:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "result_review", body, actor):
                self._check_version(case, body.expected_version)
                job, approval = self._pending_job(tenant, case, main=False)
                if not set(roles) & set(approval["roles"]):
                    raise Forbidden("CPS result review role is required")
                effective = self.validate_result(
                    job["agent"],
                    body.edited if body.edited is not None else approval["payload"],
                    job["snapshot"],
                )
                job["review"] = {
                    "action": "confirm_result" if body.accept else "reject_result",
                    "actor": actor,
                    "roles": roles,
                    "reason": body.reason,
                    "original": approval["payload"],
                    "effective": effective,
                    "accept": body.accept,
                    "approval_id": approval["id"],
                    "approval_version": approval["version"],
                    "at": time.time(),
                }
                self.records.put(tenant, "cps_job", job["id"], job)
                self._remember(tenant, case_id, "result_review", body, actor)
                self._save(tenant, case, "result_reviewed", actor, decision=job["review"])
        self.flush(tenant)
        return self.detail(tenant, case_id)

    def _flush_review(self, tenant: str, job: dict[str, Any]) -> None:
        review = job["review"]
        approval = self.records.get(tenant, "approval", review["approval_id"])
        expected = "approved" if review["accept"] else "rejected"
        if approval["state"] == "pending":
            try:
                self.runtime.approve(
                    tenant,
                    approval["id"],
                    actor=review["actor"],
                    roles=review["roles"],
                    expected_version=review["approval_version"],
                    accept=review["accept"],
                    edited=review["effective"],
                )
            except Conflict:
                approval = self.records.get(tenant, "approval", review["approval_id"])
                if approval["state"] == "pending":
                    raise
            else:
                return
        if (
            approval["state"] != expected
            or approval.get("actor") != review["actor"]
            or approval.get("effective") != review["effective"]
        ):
            raise Conflict("Kernel approval differs from the recorded CPS business review")

    def _store_result(
        self,
        case: Inspection,
        agent: str,
        result: dict[str, Any],
        review: dict[str, Any],
    ) -> None:
        key = RESULT_KEYS[agent]
        if key in ("issues", "rectification", "history_analysis", "coverage"):
            invalidated = ["report", "work_plan"]
            if key == "issues":
                invalidated.append("rectification")
            for dependent in invalidated:
                case.results.pop(dependent, None)
                case.confirmations.pop(dependent, None)
            case.archived_report = case.published_plan = None
        elif key == "report":
            case.archived_report = None
            case.results.pop("work_plan", None)
            case.confirmations.pop("work_plan", None)
            case.published_plan = None
        elif key == "work_plan":
            case.published_plan = None
        case.results[key] = result[key]
        case.confirmations[key] = {
            "actor": review["actor"],
            "confirmed_at": review["at"],
            "reason": review["reason"],
            "evidence_version": case.evidence_version,
            "fingerprint": digest(result[key]),
        }

    def manual_result(
        self,
        tenant: str,
        case_id: str,
        actor: str,
        body: ManualResult,
    ) -> dict[str, Any]:
        with self.records.transaction():
            case = self.repository.get(tenant, case_id)
            if not self._replayed(tenant, case_id, "manual_result", body, actor):
                self._check_version(case, body.expected_version)
                self._idle(case)
                result = self.validate_result(
                    body.agent, body.output, self._snapshot(tenant, case, "")
                )
                if result["status"] != "completed":
                    raise ValueError("Manual confirmation must provide a completed result")
                review = {"actor": actor, "reason": body.reason, "at": time.time()}
                self._store_result(case, body.agent, result, review)
                case.history.append(
                    {"agent": body.agent, "manual": True, "result": result, **review}
                )
                self._remember(tenant, case_id, "manual_result", body, actor)
                self._save(tenant, case, "manual_result", actor, agent=body.agent, result=result)
        return self.detail(tenant, case_id)

    def _dispatch_memory(
        self,
        tenant: str,
        case: Inspection,
        job: dict[str, Any],
        outcome: Any,
    ) -> None:
        decision = job.get("dispatch") or job.get("review")
        if not decision or "selected_agent" not in decision:
            return
        Memory(self.records).propose(
            tenant,
            "cps",
            {
                "type": "dispatch_decision",
                "scope": case.line_info.line_id,
                "condition": {"goal": case.goal, "line_info": case.line_info.model_dump()},
                "decision": decision,
                "agent_output": job.get("review", {}).get("original")
                if job.get("review")
                else None,
                "human_result_review": job.get("review") if job["agent"] != "main" else None,
                "actual_output": outcome,
                "effectiveness": "尚无长期复查证据，不能据此认定决策路径有效",
            },
            source_run=job["run_id"],
            key=f"cps-dispatch-{job['id']}",
            transaction=False,
        )

    def advance(self, tenant: str, run_id: str) -> None:
        run = self.runtime.get(tenant, run_id)
        if not run["workflow"].startswith("cps_"):
            return
        if run["status"] not in ("succeeded", "failed", "cancelled", "uncertain"):
            return
        job_id = run["inputs"].get("job_id")
        with self.records.transaction():
            job = self.records.get(tenant, "cps_job", job_id)
            if not job or job["applied"]:
                return
            submission = self.records.get(
                tenant, "submission", digest([job["inspection_id"], job_id])
            )
            if not submission or submission["run_id"] != run_id:
                return
            case = self.repository.get(tenant, job["inspection_id"])
            if job["agent"] != "observation" and case.active_job != job_id:
                return
            job["run_id"] = run_id
            review = job.get("review")
            if (
                run["status"] == "succeeded"
                and job["agent"] != "observation"
                and not review
                and not job.get("stop")
            ):
                raise Forbidden("Native approval alone does not constitute a CPS business review")
            if job["agent"] == "observation":
                if run["status"] == "succeeded":
                    result = self.validate_result("observation", run["output"], job["snapshot"])
                    for index, candidate in enumerate(result["memory_candidates"]):
                        Memory(self.records).propose(
                            tenant,
                            "cps",
                            {**candidate, "scope": case.line_info.line_id},
                            source_run=run_id,
                            key=f"cps-observation-{job_id}-{index}",
                            transaction=False,
                        )
                job.update(applied=True, result_status=run["status"])
                self.records.put(tenant, "cps_job", job_id, job)
                return
            case.active_job = None
            if job.get("stop"):
                case.status = "cancelled" if job["stop"]["action"] == "cancel" else "needs_input"
                case.history.append({"job_id": job_id, "agent": job["agent"], "stop": job["stop"]})
                self._dispatch_memory(tenant, case, job, {"status": case.status})
            elif run["status"] != "succeeded":
                case.status = "needs_human"
                case.history.append(
                    {"job_id": job_id, "agent": job["agent"], "status": run["status"]}
                )
                self._dispatch_memory(tenant, case, job, {"status": run["status"]})
            elif job["agent"] == "main":
                action = review["action"]
                selected = review["selected_agent"]
                case.history.append({"job_id": job_id, "agent": "main", "decision": review})
                if action == "skip":
                    self._enqueue(tenant, case, "main", review["instruction"])
                    self._dispatch_memory(tenant, case, job, {"status": "skipped"})
                elif action in ("return", "manual"):
                    case.status = "needs_input" if action == "return" else "needs_human"
                    self._dispatch_memory(tenant, case, job, {"status": case.status})
                elif selected:
                    self._can_dispatch(case, selected)
                    next_job = self._enqueue(tenant, case, selected, review["instruction"])
                    next_record = self.records.get(tenant, "cps_job", next_job)
                    next_record["dispatch"] = review
                    self.records.put(tenant, "cps_job", next_job, next_record)
                elif review["original"]["action"] == "finish":
                    if not case.can_finish():
                        raise Conflict("Inspection completion prerequisites are not satisfied")
                    case.status = "completed"
                    case.completed_at = time.time()
                    self._dispatch_memory(tenant, case, job, {"status": "completed"})
                    self._enqueue(tenant, case, "observation")
                else:
                    case.status = (
                        "needs_input"
                        if review["original"]["action"] == "request_input"
                        else "needs_human"
                    )
                    self._dispatch_memory(tenant, case, job, {"status": case.status})
            else:
                result = self.validate_result(job["agent"], run["output"], job["snapshot"])
                case.history.append(
                    {
                        "job_id": job_id,
                        "agent": job["agent"],
                        "result": result,
                        "review": review,
                    }
                )
                self._dispatch_memory(tenant, case, job, result)
                if result["status"] == "completed":
                    self._store_result(case, job["agent"], result, review)
                    self._enqueue(tenant, case, "main")
                else:
                    case.status = (
                        "needs_input" if result["status"] == "needs_input" else "needs_human"
                    )
            job.update(applied=True, result_status=run["status"], output=run.get("output"))
            self.records.put(tenant, "cps_job", job_id, job)
            self._save(tenant, case, "run_applied", "cps-service", job_id=job_id, run_id=run_id)
        self.flush(tenant)
