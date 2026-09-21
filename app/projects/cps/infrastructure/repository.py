from __future__ import annotations

from app.harness.kernel.domain.models import Forbidden
from app.harness.kernel.domain.ports import Repository
from app.projects.cps.domain.models import Inspection


class KernelInspectionRepository:
    def __init__(self, records: Repository):
        self.records = records

    def get(self, tenant: str, inspection_id: str) -> Inspection:
        record = self.records.get(tenant, "cps_case", inspection_id)
        if record is None:
            raise Forbidden("Inspection is not accessible in this tenant")
        return Inspection.model_validate(record)

    def save(self, tenant: str, case: Inspection) -> None:
        self.records.put(tenant, "cps_case", case.id, case.model_dump(mode="json"))

    def list(self, tenant: str) -> list[Inspection]:
        return [
            Inspection.model_validate(record) for record in self.records.scan(tenant, "cps_case")
        ]

    def get_record(self, tenant: str, kind: str, key: str):
        return self.records.get(tenant, f"cps_{kind}", key)
