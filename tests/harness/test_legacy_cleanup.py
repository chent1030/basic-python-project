from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_kernel_import_does_not_load_legacy_modules() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json; import app.harness.kernel; "
            "print(json.dumps(sorted(sys.modules)))",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    modules = json.loads(result.stdout.strip().splitlines()[-1])
    legacy_prefixes = (
        "agentscope",
        "app.harness.agents",
        "app.harness.base",
        "app.harness.backends",
        "app.harness.communication",
        "app.harness.context",
        "app.harness.middleware",
        "app.harness.memory",
        "app.harness.tools",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in modules
        for prefix in legacy_prefixes
    )


def test_document_review_legacy_exports_remain_available() -> None:
    import app.harness as harness
    from app.harness.agents.single import BaseSingleAgent
    from app.projects.doc_review.agents import ProgrammeFactsAgent

    assert harness.BaseSingleAgent is BaseSingleAgent
    assert issubclass(ProgrammeFactsAgent, BaseSingleAgent)
    assert all(getattr(harness, name) is not None for name in harness.__all__)
    assert set(harness.__all__).issubset(dir(harness))


@pytest.mark.parametrize(
    "name",
    [
        "BaseParallelAgent",
        "BaseSequentialAgent",
        "BasePipelineAgent",
        "PipelineStep",
        "BaseConversationalAgent",
        "BaseRouterAgent",
        "BasePlanExecuteAgent",
        "BaseReflectionAgent",
        "BaseSubagentAgent",
        "aggregator",
    ],
)
def test_removed_topologies_are_not_exported(name: str) -> None:
    import app.harness as harness

    with pytest.raises(AttributeError, match=name):
        getattr(harness, name)


@pytest.mark.parametrize("backend", ["agentscope", "unknown"])
def test_removed_or_unknown_backend_is_rejected(backend: str) -> None:
    from app.harness.backends import build_backend

    with pytest.raises(ValueError, match="仅支持 llm \\| deepagents"):
        build_backend(SimpleNamespace(backend=backend))


@pytest.mark.parametrize("backend", ["deepagents", "llm"])
def test_supported_legacy_backend_can_be_built(backend: str) -> None:
    from app.harness.backends import BaseBackend, build_backend

    agent = SimpleNamespace(backend=backend)
    adapter = build_backend(agent)
    assert isinstance(adapter, BaseBackend)
    assert adapter.agent is agent


def test_agentscope_is_absent_from_dependency_manifest_and_lock() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert not any(
        "agentscope" in dependency.lower() for dependency in project["project"]["dependencies"]
    )
    assert "agentscope" not in {package["name"].lower() for package in lock["package"]}
