from __future__ import annotations

import base64
import io
from collections import defaultdict, deque
from copy import deepcopy

import pytest
from PIL import Image

from app.harness.kernel import Runtime, SQLiteRepository
from app.projects.cps.bootstrap import register
from app.projects.cps.domain.models import CreateInspection, EvidenceInput, VersionedCommand
from app.projects.cps.infrastructure.config import CPSConfig


def completed(**payload):
    return {
        "status": "completed",
        "summary": "分析完成，等待人工确认",
        "missing_inputs": [],
        "warnings": [],
        "human_confirmation_required": True,
        **payload,
    }


def proposal(agent="issue_identification", action="propose_agent"):
    return {
        "action": action,
        "agent_name": agent,
        "reason": "需要完成当前目标",
        "expected_output": "结构化结果",
        "prerequisites": [],
        "missing_inputs": [],
        "source_refs": ["inspection"],
        "requires_human_confirmation": True,
    }


def report_output(title="巡检报告"):
    return completed(
        report={
            "title": title,
            "summary": "人工复核版本",
            "risk": "medium",
            "sections": [
                {
                    "title": "现场",
                    "content": "待确认现场记录",
                    "claim_type": "fact",
                    "source_refs": ["inspection"],
                }
            ],
            "review_items": [],
            "draft": True,
        }
    )


def image_base64():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class ScriptedEngine:
    def __init__(self):
        self.scripts = defaultdict(deque)
        self.calls = []

    async def invoke(self, definition, inputs, context, resume=None):
        name = definition.name.removeprefix("cps_")
        self.calls.append((name, deepcopy(inputs)))
        if self.scripts[name]:
            result = self.scripts[name].popleft()
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)
        if name == "main":
            return proposal(None, "request_human")
        if name == "issue_identification":
            source = next(item for item in inputs["sources"] if item.startswith("evidence:"))
            return completed(
                issues=[
                    {
                        "issue_id": "issue1",
                        "category": "设备",
                        "description": "防护缺失",
                        "location": "工位1",
                        "severity": "high",
                        "suggested_action": "安装防护罩",
                        "confidence": None,
                        "limitations": [],
                        "source_refs": [source],
                    }
                ]
            )
        if name == "observation":
            return completed(memory_candidates=[])
        raise AssertionError(f"No test result configured for {name}")


@pytest.fixture
def environment(tmp_path):
    records = SQLiteRepository(tmp_path / "state.sqlite")
    engine = ScriptedEngine()
    runtime = Runtime(records, engine, workspace_root=tmp_path / "workspaces")
    register(runtime, config=CPSConfig())
    yield runtime.cps_service, runtime, engine
    records.close()


async def create_started(service, engine, *, required=None):
    case = service.create(
        "tenant",
        "employee",
        CreateInspection(
            goal="检查产线并形成闭环",
            line_info={"line_id": "L1", "area": "A1", "supervisor_id": "S1"},
            required_outputs=required if required is not None else ["issues"],
            idempotency_key="case1",
        ),
    )
    await service.add_evidence(
        "tenant",
        case.id,
        "employee",
        EvidenceInput(
            expected_version=case.version,
            idempotency_key="image1",
            kind="before",
            content_base64=image_base64(),
        ),
    )
    case = service.repository.get("tenant", case.id)
    engine.scripts["main"].append(proposal())
    case = service.start(
        "tenant",
        case.id,
        "employee",
        VersionedCommand(
            expected_version=case.version,
            idempotency_key="start1",
        ),
    )
    return case


async def execute_active(service, runtime, case_id):
    case = service.repository.get("tenant", case_id)
    job = service.records.get("tenant", "cps_job", case.active_job)
    run = await runtime.execute("tenant", job["run_id"])
    service.advance("tenant", run["id"])
    return run
