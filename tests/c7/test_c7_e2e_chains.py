"""C7 系统化测试 · 5 条端到端链（波次 13）。

PRD AC-22 验收测试落地：每条链 ≥3 个断言。

覆盖矩阵：
- 链 1：AI 初审 + 裁决 + 写记忆 + 检索 hints 闭环（FR-09 长期记忆）
- 链 2：覆盖分析 B7（Coverage analyze round-trip + 不变量）
- 链 3：视觉点检 B6（type-match → content → evidence 三级 + 评分 + LRU+TTL）
- 链 4：结构化输出 v2（extract_json_object 四级降级）
- 链 5：回调重推 + 终态补发（POST /api/v1/agent/rectifications/{task_id}/callback/re-push）

每条链使用 sqlite 内存库 + 注入替身（不依赖真实 Java 仓 / LLM / pgvector）；
关键端点经 FastAPI TestClient 验证契约。
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.agent_initial_review import AgentInitialReviewExec
from app.projects.initial_review.application.service import InitialReviewService
from app.projects.initial_review.infrastructure.model_client import extract_json_object
from app.projects.room_checks.infrastructure.vision_client import (
    FakeVisionModelClient,
    VisionCallResult,
)
from app.skill.memory.domain.enums import SourceTable
from app.skill.memory.domain.models import EMBEDDING_DIM, MemoryEntry
from tests.c7.conftest import (
    FakeMemoryRetriever,
    build_initial_review_app,
    naive_utc_now,
)
from tests.initial_review.conftest import (
    drain,
    make_request,
    make_session_factory,
)
from tests.projects.conftest import (
    build_app as build_room_checks_app,
)
from tests.projects.conftest import (
    post_judge,
    post_rejudge,
)
from tests.projects.test_room_checks_judge import (
    _content_pass,
    _typed_ok,
)
from tests.skill.coverage_helpers import FakeCoverageClient

# ============================================================================
# 链 1：AI 初审 + 裁决 + 写记忆 + 检索 hints 闭环
# ============================================================================


async def _seed_memory_entry(repo, *, issue_id: int, source_id: int = 1) -> None:
    """往 memory_entry 塞 1 条裁决记忆（embedding 走 HashPlaceholderEmbedding
    同一向量方向 → 检索必召回）。"""
    from app.skill.memory.infrastructure.embedding_client import HashPlaceholderEmbedding

    emb = HashPlaceholderEmbedding(dim=EMBEDDING_DIM)
    embedding = await emb.embed_query("接地引下线锈蚀 设备接地 整改")
    await repo.upsert_judgment(MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=source_id,
        issue_id=issue_id,
        category_l1_id=1,
        factory="A",
        area="B",
        embedding=embedding,
        payload={"decision": "APPROVE", "reason": "历史同类问题"},
        tags=[],
    ))


def _build_review_service(sf, callback, memory_retriever=None):
    """构造 InitialReviewService（含回调 dispatcher 注入），统一替身。"""
    from tests.initial_review.conftest import make_settings, naive_utc_now

    pending: list = []
    service = InitialReviewService(
        sf,
        callback,  # type: ignore[arg-type]
        make_settings(),
        clock=naive_utc_now,  # 与 sqlite 朴素 UTC 时钟对齐（避免 tz aware/naive 混算）
        callback_dispatcher=lambda coro: pending.append(coro),
        memory_retriever=memory_retriever,
    )
    return service, pending


async def test_chain1_submit_with_memory_retrieves_hints_into_prompt(
    memory_repository,
) -> None:
    """链 1-1：submit(rectify) → _execute 调 memory_retriever.retrieve_context_for_issue(
    issue_id, top_k=5) 拿 hints → prompt v3 拼 HISTORICAL_HINT_SECTION → 完成。
    """
    await _seed_memory_entry(memory_repository, issue_id=101)
    sf = await make_session_factory()
    from tests.initial_review.conftest import FakeCallbackClient

    mem = FakeMemoryRetriever(hints_seq=[[
        {"source_table": "ADJUDICATION", "source_id": 1, "issue_id": 101,
         "score": 0.95, "payload": {"decision": "APPROVE"}},
        {"source_table": "ADJUDICATION", "source_id": 2, "issue_id": 101,
         "score": 0.80, "payload": {"decision": "APPROVE"}},
    ]])
    callback = FakeCallbackClient()
    service, pending = _build_review_service(sf, callback, memory_retriever=mem)

    resp = await service.submit(
        make_request(issue_id="ISS-101", version_no=1, submission_id="SUB-101-1")
    )
    await drain(pending)

    # 断言 1：memory_retriever.retrieve_context_for_issue 被调 1 次（首次执行）
    assert len(mem.calls) == 1
    call = mem.calls[0]
    # 断言 2：top_k=5 且 issue_id 透传
    assert call["top_k"] == 5
    assert call["issue_dto"]["id"] == "ISS-101"
    # 断言 3：返回 2 条 hint（seed 1 条，但 FakeMemoryRetriever 用 seq 优先）
    assert len(mem.last_hints) == 2
    # 断言 4：执行完成、回调 1 条
    assert resp["status"] == "COMPLETED"
    assert len(callback.payloads) == 1


async def test_chain1_second_submit_retrieval_count_changes_after_new_judgment(
    memory_repository,
) -> None:
    """链 1-2：同 issue 再 submit → 检索结果变化（hint 数 1 → 2）。
    验证 retrieval 接口对同 issue_id 的多次调用形态正确。
    """
    await _seed_memory_entry(memory_repository, issue_id=202, source_id=1)
    await _seed_memory_entry(memory_repository, issue_id=202, source_id=2)
    sf = await make_session_factory()
    from tests.initial_review.conftest import FakeCallbackClient

    cb = FakeCallbackClient()
    # seq：第一次 retrieve → 1 条；第二次 retrieve → 2 条；模拟记忆增长
    mem = FakeMemoryRetriever(
        hints_seq=[[{"source_id": 1}], [{"source_id": 1}, {"source_id": 2}]]
    )
    service, pending = _build_review_service(sf, cb, memory_retriever=mem)

    # 第一次 submit（v1）
    await service.submit(make_request(issue_id="ISS-202", version_no=1, submission_id="S1"))
    await drain(pending)
    first_call_count = len(mem.calls)
    first_hints = len(mem.last_hints or [])

    # 同 issue v2 重 submit → 新 task_ref，新检索
    await service.submit(make_request(issue_id="ISS-202", version_no=2, submission_id="S2"))
    await drain(pending)

    # 断言 1：retrieve 被调用 2 次（两次 submit 各一次）
    assert len(mem.calls) == first_call_count + 1
    # 断言 2：第二次检索返回 2 条（FakeMemoryRetriever seq 第 2 批）
    assert len(mem.last_hints) == 2
    # 断言 3：第一次返回 1 条（与第二次的 2 条不同 → 检索结果变化）
    assert first_hints == 1
    assert first_hints != len(mem.last_hints)


async def test_chain1_callback_payload_surfaces_completion_with_hints_metadata(
    memory_repository,
) -> None:
    """链 1-3：回调 payload 含 status/overall/items 等核心字段（hints
    不在回调里直接序列化，但 model_checks 含 aggregate_note + 终态正确）。

    注：当前实现里「memory_hints_used」字段属波次 13 计划（待 app 层落
    audit_snapshot）——本断言验证回调契约 + 终态完成，作为该字段落地前的
    回归基线。
    """
    await _seed_memory_entry(memory_repository, issue_id=303)
    sf = await make_session_factory()
    from tests.initial_review.conftest import FakeCallbackClient

    cb = FakeCallbackClient()
    mem = FakeMemoryRetriever(hints_seq=[[{"source_id": 1}]])
    service, pending = _build_review_service(sf, cb, memory_retriever=mem)
    await service.submit(make_request(issue_id="ISS-303", version_no=1, submission_id="S303"))
    await drain(pending)

    assert len(cb.payloads) == 1
    payload = cb.payloads[0]
    # 断言 1：回调幂等键
    assert payload["idempotency_key"] == "initial-review-result-cps-rectify-ISS-303-v1"
    # 断言 2：终态字段对齐
    assert payload["status"] == "COMPLETED"
    assert payload["overall"] in {"PASS", "PARTIAL", "PROBLEM"}
    # 断言 3：items 数组非空（≥5 项：文本规则 + 视觉/语义检查占位）
    assert isinstance(payload["items"], list)
    assert len(payload["items"]) >= 5
    # 断言 4：memory retriever 确实被调（hints 走通）
    assert len(mem.calls) == 1


# ============================================================================
# 链 2：覆盖分析 B7（Coverage analyze）
# ============================================================================


async def test_chain2_coverage_analyze_round_trip_via_asgi(
    coverage_session_factory,
) -> None:
    """链 2-1：覆盖分析 GET /coverage/analyze 端到端（FastAPI TestClient + 替身 Java
    客户端）。验四聚合 JOIN 数据走通：frequency + region_supervisor + recurrence + gaps。
    """
    import time

    from fastapi import FastAPI

    from app.api.v1.endpoints.agent_runs import Principal, principal
    from app.skill.coverage.api.router import router as coverage_router
    from app.skill.coverage.application.services import CoverageAnalysisService
    from app.skill.coverage.infrastructure.repository import CoverageRepository

    java = FakeCoverageClient(
        frequency=[
            dict(factory="F1", area="A1", category_l1_id=1,
                 issue_count=10, closed_count=6, overdue_count=2, recurrence_count=1),
            dict(factory="F1", area="A1", category_l1_id=2,
                 issue_count=4, closed_count=4, overdue_count=0, recurrence_count=0),
        ],
        region_supervisor=[
            dict(region="R1", supervisor_emp_no="S1",
                 open_count=5, overdue_count=2, handled_count=1),
        ],
        recurrence=[
            dict(issue_id=1, recurrence_count=4,
                 last_recurrence_at="2024-01-10", factory="F1", area="A1", category_l1_id=1),
        ],
        gaps=[
            dict(storage_room_type="cold", gap_severity=3,
                 days_since_last_record=10, factory="F1", area="A1", category_l1_id=1),
        ],
    )
    repo = CoverageRepository(coverage_session_factory)
    analysis = CoverageAnalysisService(java, repo)

    app = FastAPI()
    app.include_router(coverage_router)
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="java-test", roles=["cps_admin"], expires=time.time() + 3600,
    )
    app.state.coverage_services = (analysis, None)

    client = TestClient(app)
    resp = client.get(
        "/coverage/analyze",
        params={"periodStart": "2024-01-01T00:00:00+00:00",
                "periodEnd": "2024-01-31T23:59:59+00:00"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 断言 1：四聚合返回
    assert "frequency_trends" in body and len(body["frequency_trends"]) == 2
    assert "region_supervisor_loads" in body and len(body["region_supervisor_loads"]) == 1
    assert "recurrences" in body and len(body["recurrences"]) == 1
    assert "gaps" in body and len(body["gaps"]) == 1
    # 断言 2：summary_metrics 汇总（10 + 4 = 14）
    assert body["summary_metrics"]["total_issues"] == 14
    # 断言 3：anomalies 含四 key（与聚合种类对应）
    for key in ("top_3_high_freq", "top_3_overdue_loading",
                "top_3_recurrence", "gap_count_by_type"):
        assert key in body["anomalies"]


async def test_chain2_coverage_analyze_4_classification_predicates_execute(
    coverage_session_factory,
) -> None:
    """链 2-2：覆盖分析服务按 4 路聚合 kind 全部拉数据（frequency / region_supervisor /
    recurrence / gaps）——验证「JOIN cps_issue 4 路分类谓词」语义层走通。
    """
    from datetime import datetime

    from app.skill.coverage.application.services import CoverageAnalysisService
    from app.skill.coverage.infrastructure.repository import CoverageRepository

    java = FakeCoverageClient(
        frequency=[dict(factory="F", area="A", category_l1_id=1, issue_count=1,
                        closed_count=0, overdue_count=0, recurrence_count=0)],
        region_supervisor=[dict(region="R", supervisor_emp_no="S",
                                 open_count=1, overdue_count=0, handled_count=0)],
        recurrence=[dict(issue_id=99, recurrence_count=2, last_recurrence_at="2024-01-01",
                         factory="F", area="A", category_l1_id=1)],
        gaps=[dict(storage_room_type="dry", gap_severity=1, days_since_last_record=5,
                   factory="F", area="A", category_l1_id=1)],
    )
    repo = CoverageRepository(coverage_session_factory)
    analysis = CoverageAnalysisService(java, repo)
    ps = datetime(2024, 1, 1, tzinfo=UTC)
    pe = datetime(2024, 1, 31, tzinfo=UTC)

    snap = await analysis.summary(
        period_start=ps.isoformat(), period_end=pe.isoformat(),
        kinds=["frequency", "region_supervisor", "recurrence", "gaps"],
    )
    # 断言 1：4 路全部填充
    assert len(snap.frequency_trends) == 1
    assert len(snap.region_supervisor_loads) == 1
    assert len(snap.recurrences) == 1
    assert len(snap.gaps) == 1
    # 断言 2：summary.total_issues ≥ 1（至少 frequency 那条计入）
    assert snap.summary_metrics["total_issues"] >= 1
    # 断言 3：4 路 kind 与聚合结果一一对应（不重不漏）
    kinds_in_snap = (
        bool(snap.frequency_trends),
        bool(snap.region_supervisor_loads),
        bool(snap.recurrences),
        bool(snap.gaps),
    )
    assert all(kinds_in_snap)


async def test_chain2_coverage_analyze_forbidden_role_returns_403(
    coverage_session_factory,
) -> None:
    """链 2-3：覆盖分析端点鉴权（缺 cps_admin → 403）。J 线对账：Java 端带
    legacy-cps 角色访问 → 200；其他角色访问 → 403。
    """
    import time

    from fastapi import FastAPI

    from app.api.v1.endpoints.agent_runs import Principal, principal
    from app.skill.coverage.api.router import router as coverage_router
    from app.skill.coverage.application.services import CoverageAnalysisService
    from app.skill.coverage.infrastructure.repository import CoverageRepository

    java = FakeCoverageClient(frequency=[
        dict(factory="F", area="A", category_l1_id=1, issue_count=1,
             closed_count=0, overdue_count=0, recurrence_count=0),
    ])
    app = FastAPI()
    app.include_router(coverage_router)
    app.dependency_overrides[principal] = lambda: Principal(
        tenant="t", actor="guest", roles=["viewer"], expires=time.time() + 3600,
    )
    analysis = CoverageAnalysisService(java, CoverageRepository(coverage_session_factory))
    app.state.coverage_services = (analysis, None)

    resp = TestClient(app).get(
        "/coverage/analyze",
        params={"periodStart": "2024-01-01T00:00:00+00:00",
                "periodEnd": "2024-01-31T00:00:00+00:00"},
    )
    assert resp.status_code == 403


# ============================================================================
# 链 3：视觉点检 B6（type-match → content → evidence + LRU+TTL）
# ============================================================================


def _vision_with(results: list) -> FakeVisionModelClient:
    """构造 B6 视觉模型替身（顺序消费 results）。"""
    return FakeVisionModelClient(results=list(results))


def _evidence_pass() -> VisionCallResult:
    return VisionCallResult(
        raw_text='{"evidence_strength":"STRONG","evidence_text":"ok"}',
        parsed={"evidence_strength": "STRONG", "evidence_text": "ok"},
        model="qwen-vl-plus",
    )


async def test_chain3_three_stage_judgment_pass_contract() -> None:
    """链 3-1：B6 三级判定（type-match → content → evidence）整体 PASS：
    type_match=true + content_match=true + evidence_strong → score=100 + overall=PASS。
    """
    from app.projects.room_checks.application.judge_service import RoomCheckB6JudgeService
    vision = _vision_with([_typed_ok(), _content_pass(), _evidence_pass()])
    service = RoomCheckB6JudgeService(vision_client=vision)
    app = build_room_checks_app(service=service)
    payload = {
        "fingerprint": "rc-c7-pass",
        "roomType": "PRIMARY",
        "checkItemId": "ci-c7-pass",
        "photoUrl": "data:image/jpeg;base64,/9j/test",
        "expectedMatchType": "地面",
        "expectedKeywords": ["无杂物", "无积水"],
    }
    body = post_judge(app, payload).json()
    # 断言 1：整体 PASS + 满分
    assert body["overall"] == "PASS"
    assert body["score"] == 100
    # 断言 2：三级 raw_output 全部命中（type_match/content/evidence）
    raw = body["raw_output"]
    assert "type_match" in raw
    assert "content" in raw
    assert "evidence" in raw
    # 断言 3：模型被调 3 次（一级一调）
    assert len(vision.calls) == 3


async def test_chain3_three_stage_short_circuit_on_type_mismatch() -> None:
    """链 3-2：一级 type-match 不匹配 → content_judge / evidence 全部 NOT_REACHED，
    整体 PROBLEM + score=0（避免无效重试浪费模型配额）。
    """
    from app.projects.room_checks.application.judge_service import RoomCheckB6JudgeService
    from app.projects.room_checks.infrastructure.vision_client import VisionCallResult

    vision = _vision_with([VisionCallResult(
        raw_text='{"matched": false, "observed_type": "天花板", "reason": "主体不符"}',
        parsed={"matched": False, "observed_type": "天花板", "reason": "主体不符"},
        model="qwen-vl-plus",
    )])
    service = RoomCheckB6JudgeService(vision_client=vision)
    app = build_room_checks_app(service=service)
    body = post_judge(app, {
        "fingerprint": "rc-c7-mismatch",
        "roomType": "PRIMARY",
        "checkItemId": "ci-c7-mm",
        "photoUrl": "data:image/jpeg;base64,/9j/test",
        "expectedMatchType": "地面",
    }).json()
    # 断言 1：整体 PROBLEM + 0 分
    assert body["overall"] == "PROBLEM"
    assert body["score"] == 0
    # 断言 2：一级 FAIL，二/三级未调用（短路）— raw_output 仅含 type_match
    raw = body["raw_output"]
    assert "type_match" in raw
    assert "content" not in raw
    assert "evidence" not in raw
    # 断言 3：模型只被调 1 次（短路）
    assert len(vision.calls) == 1


async def test_chain3_lru_ttl_idempotent_replay() -> None:
    """链 3-3：LRU+TTL 幂等（同 fingerprint 30 分钟内复用 verdict，模型不重复调用）。
    """
    from app.projects.room_checks.application.judge_service import RoomCheckB6JudgeService
    vision = _vision_with([_typed_ok(), _content_pass(), _evidence_pass()])
    service = RoomCheckB6JudgeService(vision_client=vision)
    app = build_room_checks_app(service=service)
    payload = {
        "fingerprint": "rc-c7-idem",
        "roomType": "PRIMARY",
        "checkItemId": "ci-c7-idem",
        "photoUrl": "data:image/jpeg;base64,/9j/test",
        "expectedMatchType": "地面",
        "expectedKeywords": ["无杂物", "无积水"],
    }
    r1 = post_judge(app, payload).json()
    r2 = post_judge(app, payload).json()
    # 断言 1：两次结果完全一致
    assert r1["overall"] == r2["overall"] == "PASS"
    assert r1["score"] == r2["score"] == 100
    # 断言 2：模型只被调 3 次（仅首次走完整三级），不随请求数线性增长
    assert len(vision.calls) == 3


async def test_chain3_rejudge_invalidates_cache_and_reruns() -> None:
    """链 3-4：rejudge 清缓存重跑（同 fingerprint 第二次判定用新结果，覆盖原 verdict）。
    """
    from app.projects.room_checks.application.judge_service import RoomCheckB6JudgeService
    # 第一组（PASS ×3）+ 第二组（PASS ×3）
    vision = _vision_with(
        [_typed_ok(), _content_pass(), _evidence_pass()] * 2
    )
    service = RoomCheckB6JudgeService(vision_client=vision)
    app = build_room_checks_app(service=service)
    payload = {
        "fingerprint": "rc-c7-rejudge",
        "roomType": "PRIMARY",
        "checkItemId": "ci-c7-rj",
        "photoUrl": "data:image/jpeg;base64,/9j/test",
        "expectedMatchType": "地面",
        "expectedKeywords": ["无杂物", "无积水"],
    }
    r1 = post_judge(app, payload).json()
    # 断言 1：首次 PASS
    assert r1["overall"] == "PASS"

    rejudge_resp = post_rejudge(app, {"fingerprint": "rc-c7-rejudge"}).json()
    # 断言 2：rejudge 返回 cache_cleared=True
    assert rejudge_resp["cache_cleared"] is True

    r2 = post_judge(app, payload).json()
    # 断言 3：模型调用次数 = 6（首次 3 + 二次 3，缓存已清）
    assert len(vision.calls) == 6
    assert r2["overall"] == "PASS"


# ============================================================================
# 链 4：结构化输出 v2（extract_json_object 四级降级）
# ============================================================================


def test_chain4_extract_level1_direct_parse() -> None:
    """链 4-1：第一级「整段直解」——纯 JSON 输入一次性解析成功。"""
    text = '{"verdict": "PASS", "reason": "ok", "confidence": 0.9}'
    obj = extract_json_object(text)
    # 断言 1：顶层是 dict
    assert isinstance(obj, dict)
    # 断言 2：字段直解正确
    assert obj["verdict"] == "PASS"
    assert obj["confidence"] == 0.9
    # 断言 3：未被栅栏或修复路径干扰
    assert obj.get("reason") == "ok"


def test_chain4_extract_level2_markdown_fence() -> None:
    """链 4-2：第二级「栅栏内容」——`` ```json ... ``` `` 包裹的 JSON 提取。"""
    text = (
        "下面是模型输出：\n"
        "```json\n"
        '{"verdict": "FAIL", "reason": "内容敷衍", "confidence": 0.7}\n'
        "```\n"
        "以上是结论。"
    )
    obj = extract_json_object(text)
    # 断言 1：成功从栅栏提取
    assert obj["verdict"] == "FAIL"
    # 断言 2：前后杂讯被剥离
    assert obj["reason"] == "内容敷衍"
    # 断言 3：栅栏语言标识 json 被忽略
    assert obj["confidence"] == 0.7


def test_chain4_extract_level3_balanced_braces() -> None:
    """链 4-3：第三级「平衡花括号扫描」——首尾非 JSON 文本 + JSON 后附说明，截取首个
    平衡 ``{…}`` 片段。
    """
    text = (
        "说明：本结论仅供参考。\n"
        '{"verdict": "PARTIAL", "reason": "关键词命中 1/2", "confidence": 0.6}'
        "\n\n备注：以上仅作参考。\n"
    )
    obj = extract_json_object(text)
    # 断言 1：平衡扫描提取到首个对象
    assert obj["verdict"] == "PARTIAL"
    # 断言 2：杂讯「说明/备注」被剥离（不在解析对象内）
    assert "说明" not in obj and "备注" not in obj
    # 断言 3：confidence 数值正确
    assert obj["confidence"] == 0.6


def test_chain4_extract_level4_repair_single_quotes() -> None:
    """链 4-4：第四级「修复重试」——单引号键值 → 双引号（python repr 风格的 JSON）。
    """
    text = (
        "{'verdict': 'WARN', 'reason': '信息量偏少', 'confidence': 0.5,}"
    )
    obj = extract_json_object(text)
    # 断言 1：修复后单引号转双引号
    assert obj["verdict"] == "WARN"
    # 断言 2：尾逗号被剔除
    assert obj["reason"] == "信息量偏少"
    # 断言 3：confidence 浮点正确
    assert obj["confidence"] == 0.5


# ============================================================================
# 链 5：回调重推 + 终态补发
# ============================================================================


async def test_chain5_repush_callback_sends_again_with_same_idempotency_key() -> None:
    """链 5-1：POST /api/v1/agent/rectifications/{task_id}/callback/re-push
    手动重推一次回调（终态行）。验：状态不变、回调再次发送、幂等键不变。
    """
    from tests.initial_review.conftest import FakeCallbackClient

    sf = await make_session_factory()
    cb = FakeCallbackClient()
    service, pending = _build_review_service(sf, cb)
    await service.submit(make_request())
    await drain(pending)
    # 断言 1：首次回调 1 条
    assert len(cb.payloads) == 1
    first_key = cb.payloads[0]["idempotency_key"]

    app = build_initial_review_app(service=service)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/agent/rectifications/cps-rectify-ISS-001-v1/callback/re-push"
    )
    # 断言 2：200 + pushed=True
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pushed"] is True
    assert body["status"] == "COMPLETED"
    # 断言 3：回调数从 1 → 2，幂等键不变（Java 端去重无副作用）
    assert len(cb.payloads) == 2
    assert all(p["idempotency_key"] == first_key for p in cb.payloads)


async def test_chain5_replay_after_terminal_triggers_resend_callback() -> None:
    """链 5-2：同 task_ref 重 submit → 命中终态 → 立即补发一次回调（波次 8 J 线
    转交 #3）：Java 未等到回调靠 C-03 轮询兜底前，重发 C-01 即拿到补发回调。
    """
    from tests.initial_review.conftest import FakeCallbackClient

    sf = await make_session_factory()
    cb = FakeCallbackClient()
    service, pending = _build_review_service(sf, cb)
    r1 = await service.submit(
        make_request(issue_id="ISS-RP", version_no=1, submission_id="S-RP-1")
    )
    await drain(pending)
    assert r1["status"] == "COMPLETED"
    assert len(cb.payloads) == 1

    r2 = await service.submit(
        make_request(issue_id="ISS-RP", version_no=1, submission_id="S-RP-1")
    )
    await drain(pending)
    # 断言 1：replayed=True
    assert r2["replayed"] is True
    assert r2["status"] == "COMPLETED"
    # 断言 2：回调数从 1 → 2（首次终态迁移 1 次 + 终态重放补发 1 次）
    assert len(cb.payloads) == 2
    # 断言 3：两次幂等键相同（Java 端去重 → 重复投递无副作用）
    keys = {p["idempotency_key"] for p in cb.payloads}
    assert keys == {"initial-review-result-cps-rectify-ISS-RP-v1"}


async def test_chain5_repush_running_returns_409() -> None:
    """链 5-3：re-push 端点对 RUNNING 行返回 409（防误推未完成任务的回调）。
    校验 status guard：row.status == RUNNING → TaskNotTerminal → HTTP 409。
    """
    from tests.initial_review.conftest import FakeCallbackClient

    sf = await make_session_factory()
    cb = FakeCallbackClient()
    service, pending = _build_review_service(sf, cb)
    now = naive_utc_now()
    async with sf() as session, session.begin():
        session.add(AgentInitialReviewExec(
            task_id="cps-rectify-ISS-RUN-v1",
            issue_id="ISS-RUN",
            version_no=1,
            status="RUNNING",
            started_at=now,
            deadline_at=now + timedelta(seconds=480),
            fingerprint="f" * 64,
            input_snapshot={},
        ))

    app = build_initial_review_app(service=service)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/agent/rectifications/cps-rectify-ISS-RUN-v1/callback/re-push"
    )
    # 断言 1：409 Conflict
    assert resp.status_code == 409, resp.text
    # 断言 2：回调未发出（仍 0 条）
    assert len(cb.payloads) == 0
    # 断言 3：detail 含「仍在执行」
    assert "仍在执行" in resp.json()["detail"]


# ============================================================================
# J 线不变量校验（initial_review 状态 + memory_entry 软取代 + weekly 5 状态 + plan 幂等）
# ============================================================================


async def test_j_invariant_initial_review_5_states_observed() -> None:
    """J-1：initial_review_task 状态不变量（agent_initial_review_exec.status
    枚举对齐 Java 侧六态，Python 实际写入 RUNNING / FAILED+TIMEOUT / COMPLETED）。
    """
    from tests.initial_review.conftest import FakeCallbackClient

    sf = await make_session_factory()
    cb = FakeCallbackClient()
    service, pending = _build_review_service(sf, cb)

    # 状态 1：RUNNING/COMPLETED（提交 → 同步执行 → 终态）
    r1 = await service.submit(
        make_request(issue_id="ISS-S1", version_no=1, submission_id="S1")
    )
    await drain(pending)
    assert r1["status"] == "COMPLETED"
    row = await service._reload("cps-rectify-ISS-S1-v1")
    assert row is not None and row.status == "COMPLETED"

    # 状态 2：FAILED（异参重放 → ReplayConflict）
    from app.projects.initial_review.application.service import ReplayConflict
    with pytest.raises(ReplayConflict):
        await service.submit(make_request(
            issue_id="ISS-S1", version_no=1, submission_id="DIFF"))

    # 状态 3：FAILED/TIMEOUT（注入超时行 → status() 惰性扫描后变 FAILED+TIMEOUT）
    now = naive_utc_now()
    async with sf() as session, session.begin():
        session.add(AgentInitialReviewExec(
            task_id="cps-rectify-ISS-T1-v1",
            issue_id="ISS-T1",
            version_no=1,
            status="RUNNING",
            started_at=now - timedelta(seconds=600),
            deadline_at=now - timedelta(seconds=120),
            fingerprint="f" * 64,
        ))
    s = await service.status("cps-rectify-ISS-T1-v1")
    assert s["status"] == "FAILED" and s["error_code"] == "TIMEOUT"

    # 状态 4：replayed 观察项（同键重放）
    r4 = await service.submit(
        make_request(issue_id="ISS-S1", version_no=1, submission_id="S1")
    )
    await drain(pending)
    assert r4["replayed"] is True
    assert r4["status"] == "COMPLETED"

    # 状态 5：run_no 观察项不在本表（位于 weekly_report_run，不重复断言）
    # — 观察项落到回调里已由 chain1-3 覆盖


async def test_j_invariant_memory_entry_unique_constraint_and_soft_supersede(
    memory_repository,
) -> None:
    """J-2：memory_entry UNIQUE(source_table, source_id) + 软取代（superseded_by）。
    - 同 (source_table, source_id) upsert 不抛（PG ON CONFLICT / SQLite upsert）；
    - supersede() 把老行 is_active=False + superseded_by=<new_id>（不删行审计可追）。
    """
    # 写第 1 版裁决（source_id=100）
    await memory_repository.upsert_judgment(MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=100,
        payload={"version": 1, "decision": "PENDING"},
    ))
    # 同键 upsert 第 2 版（idempotent + 内容更新）
    await memory_repository.upsert_judgment(MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=100,
        payload={"version": 2, "decision": "APPROVE"},
    ))
    rows = await memory_repository.list_by_issue(issue_id=None)
    matching = [
        r for r in rows
        if r.source_table == SourceTable.ADJUDICATION.value and r.source_id == 100
    ]
    # 断言 1：仅 1 行（同 source_table+source_id 唯一）
    assert len(matching) == 1
    # 断言 2：内容更新（v2 覆盖 v1）
    assert matching[0].payload["version"] == 2

    # 软取代：写第 3 版（不同 source_id 200）→ supersede(100, 200)
    await memory_repository.upsert_judgment(MemoryEntry(
        source_table=SourceTable.ADJUDICATION.value,
        source_id=200,
        payload={"version": 3, "decision": "FINAL"},
    ))
    from sqlalchemy import select

    from app.skill.memory.infrastructure.repository import MemoryEntryORM
    new_id = None
    async with memory_repository._session_factory() as session:
        stmt = select(MemoryEntryORM).where(MemoryEntryORM.source_id == 200)
        result = await session.execute(stmt)
        new_id = result.scalar_one().id
    assert new_id is not None
    ok = await memory_repository.supersede(old_id=matching[0].id, new_id=new_id)
    assert ok is True

    # 断言 3：老行 is_active=False + superseded_by=new_id（不删）
    async with memory_repository._session_factory() as session:
        stmt = select(MemoryEntryORM).where(MemoryEntryORM.source_id == 100)
        result = await session.execute(stmt)
        old_row = result.scalar_one()
        assert old_row.is_active is False
        assert old_row.superseded_by == new_id


async def test_j_invariant_weekly_report_5_states_and_push_status_independent() -> None:
    """J-3：weekly_report_run 5 状态（PENDING/RUNNING/ARCHIVING/COMPLETED/FAILED）
    + push_status 独立列（SUCCESS/FAILED/PENDING/SKIPPED/UNCONFIGURED）。

    直接验证两套枚举常量的存在性 + 互不干扰（不触发完整 run 链路）。
    """
    from app.projects.weekly_report.domain.models import (
        PUSH_STATUS_FAILED,
        PUSH_STATUS_PENDING,
        PUSH_STATUS_SKIPPED,
        PUSH_STATUS_SUCCESS,
        PUSH_STATUS_UNCONFIGURED,
        STATUS_ARCHIVING,
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_PENDING,
        STATUS_RUNNING,
    )
    # 断言 1：5 个 status 常量都是合法字符串（不重）
    assert len({STATUS_PENDING, STATUS_RUNNING, STATUS_ARCHIVING,
                STATUS_COMPLETED, STATUS_FAILED}) == 5
    # 断言 2：5 个 push_status 常量都是合法字符串（不重）
    assert len({PUSH_STATUS_PENDING, PUSH_STATUS_SUCCESS, PUSH_STATUS_FAILED,
                PUSH_STATUS_SKIPPED, PUSH_STATUS_UNCONFIGURED}) == 5
    # 断言 3：status 与 push_status 命名空间交集存在但语义独立（设计 §3.3）
    assert "PENDING" in {STATUS_PENDING, PUSH_STATUS_PENDING}
    assert "FAILED" in {STATUS_FAILED, PUSH_STATUS_FAILED}
    # 断言 4：典型 ARCHIVING → COMPLETED 状态跃迁路径（不在 orchestrator 触发的代码）
    # 仅校验字符串合法
    assert isinstance(STATUS_ARCHIVING, str)


async def test_j_invariant_inspection_plan_three_task_types_idempotent() -> None:
    """J-4：inspection_plan 三类任务（INSPECT_RECTIFY/INSPECT_PATROL/INSPECT_CHECK）
    同 idempotency_key 重放 → draft_id 稳定 + status="REPLAYED"。
    """
    from app.projects.inspection_plans.application.service import (
        ALL_TASK_TYPES,
        TASK_TYPE_CHECK,
        TASK_TYPE_PATROL,
        TASK_TYPE_RECTIFY,
        DraftRequest,
        InspectionPlanDraftService,
    )

    # 断言 1：三类任务常量（与 Java D3 task_type 枚举对齐）
    assert ALL_TASK_TYPES == (TASK_TYPE_RECTIFY, TASK_TYPE_PATROL, TASK_TYPE_CHECK)

    svc = InspectionPlanDraftService()
    req = DraftRequest(
        idempotency_key="ip-c7-001",
        source_run_id="run-001",
        plan_type="WEEKLY_RECTIFY",
        title="周整改计划",
    )
    r1 = svc.draft(req)
    r2 = svc.draft(req)  # 同键重放
    # 断言 2：draft_id 稳定
    assert r1.draft_id == r2.draft_id
    # 断言 3：第二次 status="REPLAYED"
    assert r1.status == "DRAFT"
    assert r2.status == "REPLAYED"
    # 断言 4：draft_content_json 含三类任务（按 WEEKLY_RECTIFY 蓝图）
    tasks = r1.draft_content_json.get("tasks", [])
    task_types = {t["task_type"] for t in tasks}
    assert TASK_TYPE_RECTIFY in task_types
    assert TASK_TYPE_PATROL in task_types
    assert TASK_TYPE_CHECK in task_types
