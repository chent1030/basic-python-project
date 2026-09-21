from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path

from .application.runtime import Runtime
from .application.worker import Worker
from .infrastructure.deepagents import DeepAgentsEngine
from .infrastructure.models import ModelConfiguration, Models
from .infrastructure.sqlite import SQLiteRepository


async def build_runtime(
    *,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    modules: tuple[str, ...] | None = None,
) -> Runtime:
    config_path = config_path or os.environ.get("AGENT_CONFIG", "config/agents.yaml")
    root = Path(data_dir or os.environ.get("AGENT_DATA_DIR", ".agent-data")).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    models = Models(ModelConfiguration.load(config_path))
    repository = SQLiteRepository(root / "state.sqlite")
    try:
        runtime = Runtime(repository, DeepAgentsEngine(models), workspace_root=root / "workspaces")
        selected = (
            modules
            if modules is not None
            else tuple(
                filter(
                    None,
                    os.environ.get(
                        "AGENT_WORKFLOW_MODULES",
                        "app.projects.agent_examples,app.projects.cps.bootstrap",
                    ).split(","),
                )
            )
        )
        for module_name in selected:
            register = importlib.import_module(module_name.strip()).register
            result = register(runtime)
            if inspect.isawaitable(result):
                await result
        if not runtime.workflows:
            raise ValueError("No workflows registered by AGENT_WORKFLOW_MODULES")
        return runtime
    except BaseException:
        repository.close()
        raise


def build_worker(runtime: Runtime) -> Worker:
    return Worker(
        runtime,
        concurrency=int(os.environ.get("AGENT_WORKER_CONCURRENCY", "4")),
        observers=tuple(runtime.observers),
    )
