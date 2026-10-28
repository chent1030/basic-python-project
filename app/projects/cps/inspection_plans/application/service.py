"""C-07 巡检计划草稿应用服务。

口径：
- 输入：Java 侧 CpsInspectionPlanService 经 CpsAgentFrameworkClient.requestInspectionPlanDraft
  投递，载荷含 idempotency_key / source_run_id / plan_type / title / factory(可选) /
  area(可选) / risk_basis(可选)。
- 输出：草稿结构（draft_content_json 含 3 类任务建议，对齐 Java D3 任务枚举
  INSPECT_RECTIFY/PATROL/CHECK），draft_id 稳定（按 idempotency_key 重放一致）。
- 本期实现：确定性模板拼装（无 LLM）；D1 计划 Agent 后续可替换生成器，只要满足
  DraftGenerator Protocol 即可。
- 鉴权依赖见 endpoint 层（Identity.require('cps_admin')）。

幂等策略：进程内 LRU+TTL 缓存（开发：环境足以复现；多实例部署改为 Redis/DB）。
TTL=24h，覆盖周报→计划跨天的窗口；过期自动重生。
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# 与 Java D3 cps_inspection_plan_task.task_type 枚举对齐
TASK_TYPE_RECTIFY = "INSPECT_RECTIFY"
TASK_TYPE_PATROL = "INSPECT_PATROL"
TASK_TYPE_CHECK = "INSPECT_CHECK"
ALL_TASK_TYPES = (TASK_TYPE_RECTIFY, TASK_TYPE_PATROL, TASK_TYPE_CHECK)

# plan_type → 三类任务优先级权重（D1 暂定值，待业务样本校准）
_PLAN_TYPE_BLUEPRINTS: dict[str, list[tuple[str, str]]] = {
    # plan_type: [(task_type, priority), ...]  按推荐顺序
    "WEEKLY_RECTIFY": [
        (TASK_TYPE_RECTIFY, "high"),
        (TASK_TYPE_PATROL, "normal"),
        (TASK_TYPE_CHECK, "low"),
    ],
    "WEEKLY_PATROL": [
        (TASK_TYPE_PATROL, "high"),
        (TASK_TYPE_CHECK, "normal"),
        (TASK_TYPE_RECTIFY, "low"),
    ],
    "WEEKLY_CHECK": [
        (TASK_TYPE_CHECK, "high"),
        (TASK_TYPE_PATROL, "normal"),
        (TASK_TYPE_RECTIFY, "low"),
    ],
}

GENERIC_BLUEPRINT: list[tuple[str, str]] = [
    (TASK_TYPE_RECTIFY, "normal"),
    (TASK_TYPE_PATROL, "normal"),
    (TASK_TYPE_CHECK, "normal"),
]

DRAFT_MODEL_VERSION = "plan-draft/skill@1"
CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 1024


@dataclass(frozen=True)
class DraftRequest:
    idempotency_key: str
    source_run_id: str
    plan_type: str
    title: str
    factory: str | None = None
    area: str | None = None
    risk_basis: str | None = None


@dataclass(frozen=True)
class DraftTask:
    task_type: str
    priority: str  # "high" | "normal" | "low"
    rationale: str


@dataclass(frozen=True)
class DraftResult:
    draft_id: str
    plan_type: str
    title: str
    source_run_id: str
    draft_content_json: dict[str, Any]
    generated_at: str  # ISO8601
    model_version: str
    status: str  # "DRAFT" | "REPLAYED"


def _stable_draft_id(idempotency_key: str) -> str:
    """稳定 draft_id：同一幂等键 → 同一 draft_id（重放一致）。"""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"draft-{digest[:16]}"


def _blueprint(plan_type: str) -> list[tuple[str, str]]:
    return _PLAN_TYPE_BLUEPRINTS.get(plan_type, GENERIC_BLUEPRINT)


def _build_draft_content(req: DraftRequest) -> dict[str, Any]:
    """拼装 draft_content_json：含 tasks 建议 + 上下文 + 风险口径。"""
    tasks: list[dict[str, Any]] = []
    for task_type, priority in _blueprint(req.plan_type):
        rationale_parts = [
            f"基于计划类型 {req.plan_type} 推荐 {task_type}",
            f"优先级 {priority}",
        ]
        if req.risk_basis:
            rationale_parts.append(f"风险口径：{req.risk_basis}")
        if req.factory:
            rationale_parts.append(f"工厂 {req.factory}")
        if req.area:
            rationale_parts.append(f"区域 {req.area}")
        tasks.append(
            {
                "task_type": task_type,
                "priority": priority,
                "rationale": "；".join(rationale_parts),
            }
        )
    return {
        "tasks": tasks,
        "context": {
            "factory": req.factory,
            "area": req.area,
            "risk_basis": req.risk_basis,
        },
        "schema_version": "plan-draft.v1",
    }


@runtime_checkable
class DraftGenerator(Protocol):
    """D1 计划 Agent 接口（未来 LLM 实现替换点）。"""

    def generate(self, req: DraftRequest) -> dict[str, Any]: ...


class DeterministicDraftGenerator:
    """本期实现：模板拼装，无 LLM 调用。"""

    def generate(self, req: DraftRequest) -> dict[str, Any]:
        return _build_draft_content(req)


class _DraftCache:
    """进程内 LRU+TTL 缓存：idempotency_key → (DraftResult, expires_at)。"""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES, ttl: int = CACHE_TTL_SECONDS):
        self._max = max_entries
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[DraftResult, float]] = OrderedDict()

    def get(self, key: str) -> DraftResult | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            result, expires_at = entry
            if expires_at <= time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return result

    def put(self, key: str, result: DraftResult) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (result, time.time() + self._ttl)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class InspectionPlanDraftService:
    """C-07 服务入口。"""

    def __init__(self, generator: DraftGenerator | None = None):
        self._generator: DraftGenerator = generator or DeterministicDraftGenerator()
        self._cache = _DraftCache()

    def draft(self, req: DraftRequest) -> DraftResult:
        cached = self._cache.get(req.idempotency_key)
        if cached is not None:
            # 重放：返回同 draft_id + content，但 status=REPLAYED 让 Java 可识别
            return DraftResult(
                draft_id=cached.draft_id,
                plan_type=cached.plan_type,
                title=cached.title,
                source_run_id=cached.source_run_id,
                draft_content_json=cached.draft_content_json,
                generated_at=cached.generated_at,
                model_version=cached.model_version,
                status="REPLAYED",
            )
        draft_content_json = self._generator.generate(req)
        result = DraftResult(
            draft_id=_stable_draft_id(req.idempotency_key),
            plan_type=req.plan_type,
            title=req.title,
            source_run_id=req.source_run_id,
            draft_content_json=draft_content_json,
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            model_version=DRAFT_MODEL_VERSION,
            status="DRAFT",
        )
        self._cache.put(req.idempotency_key, result)
        return result


def _new_request_id() -> str:
    return uuid.uuid4().hex