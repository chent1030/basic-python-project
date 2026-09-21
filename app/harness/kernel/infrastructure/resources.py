from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
    WriteResult,
)
from langgraph.cache.sqlite import SqliteCache

from ..domain.models import Forbidden, digest
from .store import ScopedStore


class ReadOnlyFiles(FilesystemBackend):
    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="permission_denied", path=file_path)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error="permission_denied", path=file_path)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error="permission_denied", path=file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


@dataclass(frozen=True)
class Resources:
    mounts: dict[str, str] = field(default_factory=dict)
    persisted_files: bool = False

    def snapshot(self) -> dict[str, Any]:
        result = {}
        for route, directory in self.mounts.items():
            if not route.startswith("/") or not route.endswith("/") or ".." in route:
                raise ValueError("Resource routes must be absolute directory prefixes")
            root = Path(directory).resolve()
            if not root.is_dir():
                raise ValueError(f"Resource directory does not exist: {directory}")
            files = {}
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise Forbidden("Resource mounts may not contain symlinks")
                if path.is_file():
                    files[str(path.relative_to(root))] = digest(path.read_bytes().hex())
            result[route] = {"root": str(root), "files": files}
        return {"mounts": result, "persisted_files": self.persisted_files}

    def __call__(self, context: Any) -> CompositeBackend:
        self.snapshot()
        routes = {
            route: ReadOnlyFiles(directory, virtual_mode=True)
            for route, directory in self.mounts.items()
        }
        if self.persisted_files:
            routes["/persisted/"] = StoreBackend(
                namespace=lambda runtime: ("files",),
                store=ScopedStore(
                    context.runtime.repository, context.scope.tenant_id, context.scope.invocation_id
                ),
            )
        return CompositeBackend(
            default=FilesystemBackend(context.workspace.root, virtual_mode=True),
            routes=routes,
        )


@dataclass(frozen=True)
class ScopedCache:
    directory: str
    version: str

    def __call__(self, scope: Any) -> SqliteCache:
        root = Path(self.directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        key = digest(
            [scope.tenant_id, scope.task_id, scope.run_id, scope.invocation_id, self.version]
        )
        return SqliteCache(path=str(root / f"{key}.sqlite"))

    def snapshot(self) -> dict[str, Any]:
        return {"directory": self.directory, "version": self.version}


class DockerSandbox(FilesystemBackend, SandboxBackendProtocol):
    def __init__(
        self,
        root_dir: Path,
        *,
        image: str,
        timeout: int = 60,
        memory: str = "512m",
        cpus: float = 1,
        output_limit: int = 100000,
    ):
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("Sandbox images must be pinned by sha256 digest")
        if timeout < 1 or cpus <= 0 or output_limit < 1:
            raise ValueError("Sandbox limits must be positive")
        if any(path.is_symlink() for path in root_dir.rglob("*")):
            raise Forbidden("Sandbox mounts may not contain symlinks")
        super().__init__(root_dir=root_dir, virtual_mode=True)
        self.workspace = root_dir.resolve()
        self.image, self.timeout, self.memory = image, timeout, memory
        self.cpus, self.output_limit = cpus, output_limit

    @property
    def id(self) -> str:
        return "framework-" + digest(str(self.workspace))[:24]

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        from uuid import uuid4

        name = f"{self.id}-{uuid4().hex[:10]}"
        duration = min(timeout or self.timeout, self.timeout)
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={self.workspace},dst=/workspace",
            self.image,
            "/bin/sh",
            "-c",
            command,
        ]
        with tempfile.TemporaryFile() as buffer:
            try:
                result = subprocess.run(
                    args, stdout=buffer, stderr=subprocess.STDOUT, timeout=duration, check=False
                )
                size = buffer.tell()
                buffer.seek(0)
                return ExecuteResponse(
                    buffer.read(self.output_limit).decode(errors="replace"),
                    exit_code=result.returncode,
                    truncated=size > self.output_limit,
                )
            except subprocess.TimeoutExpired:
                return ExecuteResponse("Sandbox execution timed out", exit_code=124)
            finally:
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False
                )


@dataclass(frozen=True)
class DockerBackend:
    image: str
    timeout: int = 60
    memory: str = "512m"
    cpus: float = 1

    def __call__(self, context: Any) -> DockerSandbox:
        return DockerSandbox(
            context.workspace.root,
            image=self.image,
            timeout=self.timeout,
            memory=self.memory,
            cpus=self.cpus,
        )

    def snapshot(self) -> dict[str, Any]:
        return vars(self)
