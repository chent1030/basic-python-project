from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import decode_token
from app.harness.kernel import Artifacts, Mailbox, Memory, Runtime, Scope
from app.harness.kernel.domain.models import Forbidden, FrameworkError, segment
from app.projects.cps.infrastructure.config import CPSConfig


class FrameworkRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

        return handle


router = APIRouter(prefix="/agent-runs", tags=["agent-framework"], route_class=FrameworkRoute)


class Principal(BaseModel):
    tenant: str
    actor: str
    roles: list[str]
    expires: float

    def require(self, *roles: str) -> None:
        if not set(roles) & set(self.roles):
            raise HTTPException(403, "Insufficient framework role")


_optional_bearer = HTTPBearer(auto_error=False)


def principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> Principal:
    if credentials is None:
        client = request.client.host if request.client else ""
        configuration = CPSConfig.load()
        trusted = False
        try:
            address = ipaddress.ip_address(client)
            trusted = any(
                address in ipaddress.ip_network(network)
                for network in configuration.trusted_networks
            )
        except ValueError:
            trusted = client == "localhost"
        if trusted and configuration.internal_trust:
            return Principal(
                tenant="local-factory",
                actor="legacy-cps",
                roles=["cps_employee", "cps_dispatcher", "cps_supervisor", "cps_admin"],
                expires=time.time() + 3600,
            )
        raise HTTPException(401, "Missing bearer token")
    try:
        claims = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(401, "Invalid or expired token") from exc
    if claims.get("type") != "access" or not claims.get("sub") or not claims.get("tenant_id"):
        raise HTTPException(403, "An access token with sub and tenant_id is required")
    try:
        tenant = segment(claims["tenant_id"])
        roles = claims.get("roles", [])
        if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
            raise ValueError("Invalid roles")
        return Principal(
            tenant=tenant, actor=str(claims["sub"]), roles=roles, expires=claims["exp"]
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(403, "Invalid framework identity claims") from exc


def runtime(request: Request) -> Runtime:
    instance = getattr(request.app.state, "agent_runtime", None)
    if instance is None:
        raise HTTPException(503, "Agent runtime is not initialized")
    return instance


Identity = Annotated[Principal, Depends(principal)]
Service = Annotated[Runtime, Depends(runtime)]


class Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    workflow: str
    inputs: Any
    idempotency_key: str = Field(min_length=1, max_length=200)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(default=1, ge=1)
    accept: bool = True
    edited: Any = None


class Reconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry: bool = False
    output: Any = None


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str
    payload: Any
    idempotency_key: str = Field(min_length=1, max_length=200)


class ArtifactGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_run: str


@router.get("/workflows")
async def workflows(identity: Identity, service: Service):
    identity.require("run_reader", "run_operator")
    return [{"name": name, "version": version} for name, (version, _) in service.workflows.items()]


@router.post("", status_code=202)
async def submit(body: Submit, identity: Identity, service: Service):
    identity.require("run_operator")
    return service.submit(
        identity.tenant,
        body.task_id,
        body.workflow,
        body.inputs,
        idempotency_key=body.idempotency_key,
        actor=identity.actor,
    )


@router.get("")
async def runs(identity: Identity, service: Service, limit: int = Query(100, ge=1, le=500)):
    identity.require("run_reader", "run_operator", "approver")
    records = sorted(
        service.repository.scan(identity.tenant, "run"),
        key=lambda record: record["created"],
        reverse=True,
    )
    return records[:limit]


@router.get("/memories")
async def memories(identity: Identity, service: Service, namespace: str):
    identity.require("memory_reviewer", "run_reader")
    return [
        record
        for record in service.repository.scan(identity.tenant, "memory")
        if record["namespace"] == namespace
    ]


@router.post("/memories/{candidate_id}/review")
async def review_memory(candidate_id: str, body: Review, identity: Identity, service: Service):
    Memory(service.repository).review(
        identity.tenant,
        candidate_id,
        actor=identity.actor,
        roles=identity.roles,
        accept=body.accept,
        expected_version=body.expected_version,
    )
    return {"status": "reviewed"}


@router.get("/{run_id}")
async def get_run(run_id: str, identity: Identity, service: Service):
    identity.require("run_reader", "run_operator", "approver")
    return service.get(identity.tenant, run_id)


@router.get("/{run_id}/invocations")
async def invocations(run_id: str, identity: Identity, service: Service):
    identity.require("run_reader", "run_operator", "approver")
    service.get(identity.tenant, run_id)
    return [
        record
        for record in service.repository.scan(identity.tenant, "invocation")
        if record["run_id"] == run_id
    ]


@router.get("/{run_id}/approvals")
async def approvals(run_id: str, identity: Identity, service: Service):
    identity.require("run_reader", "run_operator", "approver")
    service.get(identity.tenant, run_id)
    return [
        record
        for record in service.repository.scan(identity.tenant, "approval")
        if record["run_id"] == run_id
    ]


@router.post("/{run_id}/approvals/{approval_id}")
async def decide(run_id: str, approval_id: str, body: Review, identity: Identity, service: Service):
    service.get(identity.tenant, run_id)
    approval = service.repository.get(identity.tenant, "approval", approval_id)
    if not approval or approval["run_id"] != run_id:
        raise HTTPException(404, "Approval not found")
    service.approve(
        identity.tenant,
        approval_id,
        actor=identity.actor,
        roles=identity.roles,
        expected_version=body.expected_version,
        accept=body.accept,
        edited=body.edited,
    )
    return {"status": "decided"}


@router.post("/{run_id}/cancel", status_code=202)
async def cancel(run_id: str, identity: Identity, service: Service):
    identity.require("run_operator")
    service.cancel(identity.tenant, run_id, identity.actor)
    return service.get(identity.tenant, run_id)


@router.post("/{run_id}/invocations/{invocation_id}/reconcile")
async def reconcile(
    run_id: str, invocation_id: str, body: Reconcile, identity: Identity, service: Service
):
    identity.require("run_operator")
    service.get(identity.tenant, run_id)
    record = service.repository.get(identity.tenant, "invocation", invocation_id)
    if not record or record["run_id"] != run_id:
        raise HTTPException(404, "Invocation not found")
    service.reconcile(
        identity.tenant, invocation_id, actor=identity.actor, output=body.output, retry=body.retry
    )
    return {"status": "reconciled"}


@router.get("/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    identity: Identity,
    service: Service,
    after: int = Query(0, ge=0),
    stream: bool = False,
):
    identity.require("run_reader", "run_operator", "approver")
    service.get(identity.tenant, run_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError as exc:
        raise HTTPException(422, "Last-Event-ID must be an integer cursor") from exc
    if not stream:
        return service.repository.events(identity.tenant, run_id, cursor)

    async def generate():
        position = cursor
        while not await request.is_disconnected() and time.time() < identity.expires:
            batch = service.repository.events(identity.tenant, run_id, position)
            for event in batch:
                position = event["cursor"]
                yield f"id: {position}\nevent: {event['kind']}\ndata: {json.dumps(event)}\n\n"
            run = service.get(identity.tenant, run_id)
            if not batch and run["status"] in ("succeeded", "failed", "cancelled", "uncertain"):
                return
            if not batch:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/messages", status_code=202)
async def send_message(run_id: str, body: MessageInput, identity: Identity, service: Service):
    identity.require("run_operator")
    run = service.get(identity.tenant, run_id)
    target = service.repository.get(identity.tenant, "invocation", body.recipient)
    if not target or target["run_id"] != run_id:
        raise HTTPException(404, "Recipient invocation not found in this run")
    mailbox = Mailbox(service.repository, Scope(identity.tenant, run["task_id"], run_id))
    message_id = mailbox.send(
        body.recipient,
        {"actor": identity.actor, "data": body.payload},
        key=f"{identity.actor}:{body.idempotency_key}",
    )
    return {"id": message_id}


@router.get("/{run_id}/messages")
async def messages(run_id: str, identity: Identity, service: Service):
    identity.require("run_reader", "run_operator")
    service.get(identity.tenant, run_id)
    return [
        record
        for record in service.repository.scan(identity.tenant, "message")
        if record["run_id"] == run_id
    ]


@router.get("/{run_id}/artifacts")
async def artifacts(run_id: str, identity: Identity, service: Service):
    identity.require("run_reader", "run_operator", "approver")
    service.get(identity.tenant, run_id)
    return [
        {key: value for key, value in record.items() if key != "content"}
        for record in service.repository.scan(identity.tenant, "artifact")
        if record["run_id"] == run_id
        and ("run_operator" in identity.roles or "*" in record["readers"])
    ]


@router.get("/{run_id}/artifacts/{artifact_id}")
async def download_artifact(run_id: str, artifact_id: str, identity: Identity, service: Service):
    from fastapi.responses import Response

    identity.require("run_reader", "run_operator", "approver")
    run = service.get(identity.tenant, run_id)
    record = service.repository.get(identity.tenant, "artifact", artifact_id)
    if not record or record["run_id"] != run_id:
        raise HTTPException(404, "Artifact not found")
    if "run_operator" not in identity.roles and "*" not in record["readers"]:
        raise HTTPException(403, "Artifact is private")
    scope = Scope(identity.tenant, run["task_id"], run_id, record["owner"])
    content = Artifacts(service.repository, scope).read(artifact_id)
    service.repository.event(
        identity.tenant,
        run_id,
        "artifact.downloaded",
        {"artifact_id": artifact_id, "actor": identity.actor},
    )
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}"'},
    )


@router.post("/{run_id}/artifacts/{artifact_id}/grants")
async def grant_artifact(
    run_id: str, artifact_id: str, body: ArtifactGrant, identity: Identity, service: Service
):
    identity.require("run_operator")
    run = service.get(identity.tenant, run_id)
    record = service.repository.get(identity.tenant, "artifact", artifact_id)
    if not record or record["run_id"] != run_id:
        raise HTTPException(404, "Artifact not found")
    scope = Scope(identity.tenant, run["task_id"], run_id, record["owner"])
    Artifacts(service.repository, scope).grant(artifact_id, body.target_run)
    return {"status": "granted"}


def install_error_handlers(app: Any) -> None:
    from fastapi.responses import JSONResponse

    async def framework_error(request: Request, exc: FrameworkError):
        code = 403 if isinstance(exc, Forbidden) else 409
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    app.add_exception_handler(FrameworkError, framework_error)
