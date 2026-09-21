from __future__ import annotations

from app.harness.kernel import Runtime

from .application.service import InspectionService
from .infrastructure.agents import CPSEngine, CPSLifecycle, register_agents
from .infrastructure.config import CPSConfig
from .infrastructure.evidence import HTTPImageEncoder, validate_image
from .infrastructure.repository import KernelInspectionRepository


def register(runtime: Runtime, *, config: CPSConfig | None = None, encoder=None) -> None:
    configuration = config or CPSConfig.load()
    if not configuration.enabled:
        return
    if getattr(runtime, "cps_service", None) is not None:
        raise ValueError("CPS is already registered")
    if encoder is None and configuration.embeddings.url:
        encoder = HTTPImageEncoder(configuration.embeddings)
    service = InspectionService(
        runtime,
        KernelInspectionRepository(runtime.repository),
        configuration,
        validate_image,
        encoder,
    )
    runtime.cps_service = service
    runtime.engine = CPSEngine(runtime.engine, service)
    register_agents(runtime)
    runtime.observers.append(CPSLifecycle(service))
    for tenant in runtime.repository.tenants():
        service.flush(tenant)
