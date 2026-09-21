from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app.api.v1.endpoints.agent_runs import FrameworkRoute, Identity, Service
from app.projects.cps.application.service import InspectionService
from app.projects.cps.domain.models import (
    AmendInspection,
    CreateInspection,
    DispatchReview,
    EvidenceInput,
    ManualResult,
    MemoryReview,
    ResultReview,
    StopInspection,
    TaskUpdate,
    VersionedCommand,
)

router = APIRouter(prefix="/cps", tags=["CPS inspection"], route_class=FrameworkRoute)
STAFF = ("cps_dispatcher", "cps_supervisor", "cps_admin")


def inspection_service(runtime: Service) -> InspectionService:
    service = getattr(runtime, "cps_service", None)
    if service is None:
        raise HTTPException(503, "CPS module is not enabled")
    return service


CPS = Annotated[InspectionService, Depends(inspection_service)]


@router.get("/agents")
async def agent_catalog(identity: Identity, service: CPS):
    identity.require(*STAFF)
    return [
        {
            "name": name.removeprefix("cps_"),
            "version": definition.version,
            "dispatchable": name not in ("cps_main", "cps_observation"),
            "output_schema": definition.output_schema.model_json_schema(),
            "review_roles": list(definition.approval.roles),
        }
        for name, definition in service.runtime.registry.items()
        if name.startswith("cps_")
    ]


def accessible(identity: Any, service: InspectionService, case_id: str):
    identity.require("cps_employee", *STAFF)
    case = service.repository.get(identity.tenant, case_id)
    if case.owner != identity.actor and not set(STAFF) & set(identity.roles):
        raise HTTPException(403, "Only the owner or CPS staff can access this inspection")
    return case


@router.post("/inspections", status_code=201)
async def create(body: CreateInspection, identity: Identity, service: CPS):
    identity.require("cps_employee", *STAFF)
    return service.create(identity.tenant, identity.actor, body)


@router.get("/inspections")
async def inspections(
    identity: Identity,
    service: CPS,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    identity.require("cps_employee", *STAFF)
    cases = [
        case
        for case in service.repository.list(identity.tenant)
        if case.owner == identity.actor or set(STAFF) & set(identity.roles)
    ]
    return sorted(cases, key=lambda case: case.created_at, reverse=True)[offset : offset + limit]


@router.get("/inspections/{case_id}")
async def detail(case_id: str, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    return service.detail(identity.tenant, case_id)


@router.patch("/inspections/{case_id}")
async def amend(case_id: str, body: AmendInspection, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    return service.amend(identity.tenant, case_id, identity.actor, body)


@router.post("/inspections/{case_id}/evidence", status_code=201)
async def upload_evidence(case_id: str, body: EvidenceInput, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    record = await service.add_evidence(identity.tenant, case_id, identity.actor, body)
    return {**record, "version": service.repository.get(identity.tenant, case_id).version}


@router.get("/inspections/{case_id}/evidence/{evidence_id}")
async def evidence(case_id: str, evidence_id: str, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    record = service.evidence(identity.tenant, case_id, evidence_id)
    return Response(
        base64.b64decode(record["base64"]),
        media_type=record["mime"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/inspections/{case_id}/start", status_code=202)
async def start(case_id: str, body: VersionedCommand, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    return service.start(identity.tenant, case_id, identity.actor, body)


@router.post("/inspections/{case_id}/sync")
async def synchronize(case_id: str, identity: Identity, service: CPS):
    case = accessible(identity, service, case_id)
    service.flush(identity.tenant)
    for job_id in case.jobs:
        job = service.records.get(identity.tenant, "cps_job", job_id)
        if job["run_id"]:
            service.advance(identity.tenant, job["run_id"])
    return service.detail(identity.tenant, case_id)


@router.post("/inspections/{case_id}/stop", status_code=202)
async def stop(case_id: str, body: StopInspection, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    identity.require(*STAFF)
    return service.stop(identity.tenant, case_id, identity.actor, body)


@router.post("/inspections/{case_id}/dispatch", status_code=202)
async def dispatch(case_id: str, body: DispatchReview, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    identity.require(*STAFF)
    return service.dispatch(identity.tenant, case_id, identity.actor, identity.roles, body)


@router.post("/inspections/{case_id}/results/review", status_code=202)
async def review_result(case_id: str, body: ResultReview, identity: Identity, service: CPS):
    case = accessible(identity, service, case_id)
    identity.require("cps_supervisor", "cps_admin")
    if case.active_job:
        job = service.records.get(identity.tenant, "cps_job", case.active_job)
        if job["agent"] in ("report", "work_plan"):
            identity.require("cps_admin")
    return service.review_result(identity.tenant, case_id, identity.actor, identity.roles, body)


@router.post("/inspections/{case_id}/results/manual")
async def manual_result(case_id: str, body: ManualResult, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    identity.require("cps_supervisor", "cps_admin")
    if body.agent in ("report", "work_plan"):
        identity.require("cps_admin")
    return service.manual_result(identity.tenant, case_id, identity.actor, body)


@router.get("/inspections/{case_id}/report.html", response_class=HTMLResponse)
async def preview_report(case_id: str, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    content, confirmed = service.report(identity.tenant, case_id)
    return HTMLResponse(
        content,
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "X-CPS-Confirmed": str(confirmed).lower(),
        },
    )


@router.post("/inspections/{case_id}/archive")
async def archive(case_id: str, body: VersionedCommand, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    identity.require("cps_admin")
    return service.archive(identity.tenant, case_id, identity.actor, body)


@router.post("/inspections/{case_id}/work-plan/publish")
async def publish_plan(case_id: str, body: VersionedCommand, identity: Identity, service: CPS):
    accessible(identity, service, case_id)
    identity.require("cps_admin")
    return service.publish_plan(identity.tenant, case_id, identity.actor, body)


@router.get("/inspections/{case_id}/events")
async def events(
    case_id: str,
    request: Request,
    identity: Identity,
    service: CPS,
    after: int = Query(0, ge=0),
    stream: bool = False,
):
    accessible(identity, service, case_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError as exc:
        raise HTTPException(422, "Last-Event-ID must be an integer") from exc
    if not stream:
        return service.events(identity.tenant, case_id, cursor)

    async def generate():
        position = cursor
        while time.time() < identity.expires and not await request.is_disconnected():
            batch = service.events(identity.tenant, case_id, position)
            for event in batch:
                position = event["cursor"]
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {position}\nevent: {event['kind']}\ndata: {payload}\n\n"
            if not batch:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
async def history(
    identity: Identity,
    service: CPS,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    identity.require("cps_supervisor", "cps_admin")
    records = list(service.records.scan(identity.tenant, "cps_archive"))
    records.sort(key=lambda record: record["archived_at"], reverse=True)
    return records[offset : offset + limit]


@router.get("/statistics")
async def statistics(identity: Identity, service: CPS):
    identity.require("cps_supervisor", "cps_admin")
    return service.statistics(identity.tenant)


@router.get("/metrics")
async def metrics(identity: Identity, service: CPS):
    identity.require("cps_admin")
    return service.metrics(identity.tenant)


@router.get("/tasks")
async def tasks(
    identity: Identity,
    service: CPS,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    identity.require("cps_employee", "cps_supervisor", "cps_admin")
    records = [
        record
        for record in service.records.scan(identity.tenant, "cps_task")
        if record["owner_id"] == identity.actor
        or {"cps_supervisor", "cps_admin"} & set(identity.roles)
    ]
    return records[offset : offset + limit]


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate, identity: Identity, service: CPS):
    identity.require("cps_employee", "cps_supervisor", "cps_admin")
    task = service.records.get(identity.tenant, "cps_task", task_id)
    if not task or (
        task["owner_id"] != identity.actor
        and not {"cps_supervisor", "cps_admin"} & set(identity.roles)
    ):
        raise HTTPException(403, "Task is not accessible")
    return service.update_task(identity.tenant, task_id, identity.actor, body)


@router.get("/memories")
async def memories(
    identity: Identity,
    service: CPS,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    identity.require("cps_admin")
    records = [
        record
        for record in service.records.scan(identity.tenant, "memory")
        if record["namespace"] == "cps"
    ]
    return records[offset : offset + limit]


@router.get("/memories/{memory_id}/versions")
async def memory_versions(memory_id: str, identity: Identity, service: CPS):
    identity.require("cps_admin")
    current = service.records.get(identity.tenant, "memory", memory_id)
    if not current or current["namespace"] != "cps":
        raise HTTPException(404, "CPS memory not found")
    versions = [
        record
        for record in service.records.scan(identity.tenant, "memory_history")
        if record["id"] == memory_id
    ]
    return sorted([*versions, current], key=lambda record: record["version"])


@router.post("/memories/{memory_id}/review")
async def review_memory(memory_id: str, body: MemoryReview, identity: Identity, service: CPS):
    identity.require("cps_admin")
    return service.review_memory(identity.tenant, memory_id, identity.actor, body)
