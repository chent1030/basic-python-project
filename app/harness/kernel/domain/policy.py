from __future__ import annotations

from dataclasses import dataclass, field

from .models import Approval, Forbidden


@dataclass(frozen=True)
class DeploymentPolicy:
    version: str = "1.0.0"
    default_approval: Approval = field(default_factory=Approval.none)
    require_approval: bool = False
    allow_native_interrupts: bool = True
    forbidden_tools: frozenset[str] = frozenset({"execute"})
    allowed_tools: frozenset[str] | None = None

    def resolve(self, selected: Approval, inherited: Approval | None = None) -> Approval:
        effective = (inherited or self.default_approval) if selected.mode == "inherit" else selected
        if effective.mode == "inherit":
            raise ValueError("Deployment default approval cannot inherit")
        if self.require_approval and effective.mode == "none":
            raise Forbidden("Explicit approval.none conflicts with mandatory deployment approval")
        return effective

    def check_tool(self, name: str) -> None:
        if name in self.forbidden_tools or (
            self.allowed_tools is not None and name not in self.allowed_tools
        ):
            raise Forbidden(f"Tool {name} is denied by deployment policy")

    def snapshot(self) -> dict:
        return {
            "version": self.version,
            "default_approval": self.default_approval.mode,
            "require_approval": self.require_approval,
            "allow_native_interrupts": self.allow_native_interrupts,
            "forbidden_tools": sorted(self.forbidden_tools),
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
        }
