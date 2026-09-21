from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from typing import Any

from app.harness.kernel import AgentDefinition, Approval, Observer, Step
from app.harness.kernel.domain.models import Conflict, FrameworkError
from app.harness.kernel.infrastructure.resources import ReadOnlyFiles
from app.projects.cps.domain.contracts import CONTRACTS
from app.projects.cps.domain.models import AgentInput

VISUAL_AGENTS = {"issue_identification", "rectification_judgement"}


def prompt_for(agent: str) -> str:
    root = files("app.projects.cps.prompts")
    common = root.joinpath("common.md").read_text(encoding="utf-8")
    role = root.joinpath(f"{agent}.md").read_text(encoding="utf-8")
    boundary = """## basic-project 运行约束
本角色名以 cps_ 前缀注册，但主 Agent 的 agent_name 仍填写目录中的无前缀业务名称。
图像由服务端解码校验、按租户和任务授权后送入视觉 Agent，不接受任意 URL。
主 Agent 可依据 evidence 中已校验图片的存在判断视觉执行条件，不需要自己解读图片。
sources 是可引用 ID 的完整白名单；只能逐字引用其中的 ID。
正式统计的 metrics 仅支持 record_count、issue_count、repeat_occurrences、recurrence_rate；
value 使用 context.statistics 原值，source_refs 必须为 ["statistics"]；
recurrence_rate 的 denominator 为 issue_count，其他指标 denominator=null。
计数指标 unit="count"，recurrence_rate 的 unit="ratio"，不得改变指标单位。
分类、区域、主管和措施分布仅解释 context.statistics 对应计数，不伪造指标。
问题和整改确认、报告确认与历史归档、计划确认与发布是不同的动作。
context.can_finish 为 false 时不得提议 finish；可以请求人工完成尚缺的发布动作。
整改通过必须逐条覆盖全部已确认问题；未覆盖的必须标记无法判断或部分到位。
工作任务 ID 唯一，依赖只引用本计划任务 ID，不能自引用或成环。
观察者仅依据 context.history 中的原始建议、人工决策、结果和确认记录提出候选记忆。
无任何可复用经验时，观察输出 memory_candidates=[]，不得为填充数量制造经验。
"""
    return "\n\n".join(
        (
            common,
            role,
            boundary,
            "## 输出 Schema\n"
            + json.dumps(CONTRACTS[agent].model_json_schema(), ensure_ascii=False),
        )
    )


def initial_state(inputs: dict[str, Any], context: Any) -> dict[str, Any]:
    payload = dict(inputs)
    images = payload.pop("image_content", [])
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    for image in images:
        content.append({"type": "text", "text": f"证据来源：{image['source_ref']}"})
        content.append({"type": "image_url", "image_url": {"url": image["data_url"]}})
    return {"messages": [{"role": "user", "content": content}]}


def readonly_workspace(context: Any) -> ReadOnlyFiles:
    return ReadOnlyFiles(root_dir=context.workspace.root, virtual_mode=True)


class CPSEngine:
    def __init__(self, delegate: Any, service: Any):
        self.delegate, self.service = delegate, service

    def snapshot(self, definition: AgentDefinition) -> dict[str, Any]:
        if self.delegate is None:
            raise ValueError("No model engine is configured")
        snapshot = self.delegate.snapshot(definition) if hasattr(self.delegate, "snapshot") else {}
        if definition.name.removeprefix("cps_") in VISUAL_AGENTS and snapshot:
            for model in (snapshot, *snapshot.get("fallbacks", [])):
                if "vision" not in model.get("capabilities", []):
                    raise ValueError(
                        f"CPS visual Agent requires a vision-capable model: {definition.name}"
                    )
        return snapshot

    async def invoke(
        self, definition: AgentDefinition, inputs: Any, context: Any, resume: Any = None
    ):
        if not definition.name.startswith("cps_"):
            return await self.delegate.invoke(definition, inputs, context, resume)
        tenant = context.scope.tenant_id
        case, job = self.service.require_job(tenant, context.scope.run_id, inputs)
        agent = job["agent"]
        if definition.name != f"cps_{agent}":
            raise Conflict("CPS job cannot be executed by a different Agent")
        if agent in VISUAL_AGENTS:
            self.service._can_dispatch(case, agent)
        payload = deepcopy(job["snapshot"])
        if agent in VISUAL_AGENTS:
            payload["image_content"] = []
            for metadata in case.evidence:
                evidence = self.service.evidence(tenant, case.id, metadata["id"])
                payload["image_content"].append(
                    {
                        "source_ref": f"evidence:{evidence['id']}",
                        "data_url": f"data:{evidence['mime']};base64,{evidence['base64']}",
                    }
                )
        output = await self.delegate.invoke(definition, payload, context, resume)
        return self.service.validate_result(agent, output, job["snapshot"])


class CPSLifecycle(Observer):
    def __init__(self, service: Any):
        self.service = service

        async def consume(event: dict[str, Any], memory: Any):
            if event["kind"] in ("run.succeeded", "run.failed", "run.cancelled", "run.uncertain"):
                service.advance(event["tenant"], event["run"])

        super().__init__(service.records, "cps_lifecycle", consume, include_observer_runs=True)

    async def drain(self, tenant: str, run_id: str) -> int:
        if not self.service.runtime.get(tenant, run_id)["workflow"].startswith("cps_"):
            return 0
        self.service.flush(tenant)
        for job in self.service.records.scan(tenant, "cps_job"):
            if job["run_id"] and not job["applied"]:
                try:
                    self.service.advance(tenant, job["run_id"])
                except (FrameworkError, ValueError) as exc:
                    self.service.record_job_error(tenant, job["id"], "projection_error", exc)
        return await super().drain(tenant, run_id)


def register_agents(runtime: Any) -> None:
    for agent, contract in CONTRACTS.items():
        roles = (
            ("cps_admin",) if agent in ("report", "work_plan") else ("cps_supervisor", "cps_admin")
        )
        if agent == "main":
            roles = ("cps_dispatcher", "cps_supervisor", "cps_admin")
        definition = AgentDefinition(
            f"cps_{agent}",
            version="2.0.0",
            system_prompt=prompt_for(agent),
            input_schema=AgentInput,
            output_schema=contract,
            model_profile="cps_vision" if agent in VISUAL_AGENTS else "reasoning_large",
            initial_state_factory=initial_state,
            backend_factory=readonly_workspace,
            approval=Approval.none() if agent == "observation" else Approval.after(roles=roles),
            planning=False,
            general_purpose=False,
            max_model_calls=8,
            max_tool_calls=8,
            recursion_limit=40,
            timeout_seconds=180,
        )
        runtime.register(f"cps_{agent}", Step("analyze", definition), version="2.0.0")
        runtime.engine.snapshot(definition)
