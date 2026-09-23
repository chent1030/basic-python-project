"""A10 边界用例测试集（主计划线 A，AC-17/24/26/27/34 对应边界）。

测试金字塔标注：本文件全部为**单元/组件级**测试（无网络、无真实模型）。
- D-22 极端文本组：纯函数单元测试（check_text_rules）；
- 服务边界组：sqlite 内存库 + 注入时钟/回调替身的组件测试。

覆盖清单（任务书 A10）：
- 全标点 / 全空格（半角/全角）/ 纯制表符 / 纯换行 / 首尾空白；
- 恰好 L=15 与 10×P==L 临界（L=30,P=3 整数等值）；
- 连续 …… 长游程 P 计数（10 个 → 5）；
- 全角半角混排相邻标点；
- 迟到结果仅留痕（重放不翻转已接管终态）、幂等重放不重发回调；
- 重试不重置计时（RUNNING 重放沿用原 deadline_at）；
- 480s Python deadline < 600s Java 接管窗口的配置护栏；
- 乱序完成（v2 先于 v1 终态）回调各带各的 task_ref 不串单；
- RustFS HTTP 不可达（5xx）→ 附件获取 DEGRADED 不伪造。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.models.agent_initial_review import AgentInitialReviewExec
from app.projects.initial_review.application.check_pipeline import CheckPipeline
from app.projects.initial_review.domain.text_rules import (
    MIN_LENGTH,
    check_text_rules,
)
from app.projects.initial_review.infrastructure.config import (
    RustFSSettings,
    load_initial_review_settings,
)
from app.projects.initial_review.infrastructure.model_client import (
    FakeModelCheckClient,
    RustFSObjectFetcher,
)
from tests.initial_review.conftest import (
    VALID_LONG,
    VALID_REASON,
    VALID_SHORT,
    drain,
    make_request,
    make_service,
    make_session_factory,
    make_settings,
)
from tests.initial_review.test_check_pipeline import (
    image_result,
    snapshot_of,
    valid_field,
)

# ======================================================================
# 一、D-22 冻结口径 —— 极端文本边界（纯函数单元测试）
# ======================================================================


def test_all_chinese_punctuation_text_fails_ratio_and_consecutive() -> None:
    """全标点文本：L=20 达标但 P=20，比例违规 + 整段一个连续游程（1 条 violation）。"""
    fc = check_text_rules("，" * 20)
    assert fc.length == 20
    assert fc.punctuation == 20
    assert fc.length_ok  # L≥15 满足
    assert not fc.ratio_ok  # 10×20=200 > 20
    assert not fc.no_consecutive_punct
    assert not fc.valid
    types = [v.type for v in fc.violations]
    assert types.count("consecutive_punctuation") == 1  # 极大游程只报一条
    assert "punctuation_ratio_exceeded" in types
    assert "length_below_minimum" not in types


def test_all_half_width_spaces_text_fails() -> None:
    """全半角空格：空格算标点（PRD §29.2.2）→ P=20 比例违规 + 连续。"""
    fc = check_text_rules(" " * 20)
    assert fc.length == 20
    assert fc.punctuation == 20
    assert not fc.ratio_ok
    assert not fc.no_consecutive_punct
    assert not fc.valid


def test_all_full_width_spaces_text_fails() -> None:
    """全全角空格（U+3000）：Phase 0 §⑥ 冻结与半角同算 → 同样不通过。"""
    fc = check_text_rules("　" * 20)
    assert fc.length == 20
    assert fc.punctuation == 20
    assert not fc.ratio_ok
    assert not fc.no_consecutive_punct


def test_tabs_only_text_passes_text_rules() -> None:
    """纯制表符（\\t 不剔除、不计 P）：L=20、P=0 → 通过文本规则。

    锁定 Phase 0 §⑥ 冻结：\\t/\\v/\\f 不属换行集合也不属标点集合
    （语义层是否敷衍由模型检查承担，本层只锁确定性口径）。
    """
    fc = check_text_rules("\t" * 20)
    assert fc.length == 20
    assert fc.punctuation == 0
    assert fc.ratio_ok
    assert fc.no_consecutive_punct
    assert fc.valid


def test_newline_only_text_is_empty_after_strip() -> None:
    """纯换行（含 \\u2028/\\u2029）：剔除后 L=0 → 空文本违规，不抛异常。"""
    fc = check_text_rules("\n\r\u2028\u2029\n")
    assert fc.length == 0
    assert fc.punctuation == 0
    assert not fc.length_ok
    assert fc.ratio_ok  # 10×0=0 ≤ 0，无除法不异常
    assert fc.valid is False
    assert fc.violations[0].type == "length_below_minimum"
    assert "文本为空" in fc.violations[0].message


def test_leading_trailing_single_space_exactly_ratio_boundary_valid() -> None:
    """首尾各一个空格 + 18 个正字：L=20、P=2 → 10×2==20 恰好达标 → 通过。

    单个首/尾空格不构成连续游程（游程需 ≥2 个标点/空格 token 相邻）。
    """
    fc = check_text_rules(" " + "字" * 18 + " ")
    assert fc.length == 20
    assert fc.punctuation == 2
    assert fc.ratio_ok  # 恰好 10% 整数等值
    assert fc.no_consecutive_punct
    assert fc.valid


def test_double_leading_space_consecutive_invalid() -> None:
    """两个连续首空格：比例恰好达标但连续游程违规 → 不通过。"""
    fc = check_text_rules("  " + "字" * 18)
    assert fc.length == 20
    assert fc.punctuation == 2
    assert fc.ratio_ok
    assert not fc.no_consecutive_punct  # 游程违规单独否决
    assert not fc.valid
    assert fc.violations[-1].type == "consecutive_punctuation"
    assert fc.violations[-1].fragment == "  "


def test_mixed_full_half_width_adjacent_punct_consecutive() -> None:
    """全角"，"紧邻半角"."：跨全/半角相邻仍判连续（D-22 相邻两标点直接不通过）。"""
    fc = check_text_rules("a" * 18 + "，.")
    assert fc.length == 20
    assert fc.punctuation == 2
    assert fc.ratio_ok
    assert not fc.no_consecutive_punct
    assert not fc.valid


def test_ratio_exact_equality_at_l30_p3_valid() -> None:
    """L=30、P=3（三个逗号两两不相邻）：10×3==30 精确整数等值 → 通过。"""
    text = "，".join(["字" * 7, "字" * 7, "字" * 7, "字" * 6])  # 27 字 + 3 逗号
    fc = check_text_rules(text)
    assert fc.length == 30
    assert fc.punctuation == 3
    assert fc.ratio_ok
    assert fc.no_consecutive_punct
    assert fc.valid


def test_exactly_min_length_zero_punct_valid() -> None:
    """恰好 L=15、P=0：最小长度边界（L≥15 含等号）→ 通过。"""
    fc = check_text_rules("字" * MIN_LENGTH)
    assert fc.length == 15
    assert fc.punctuation == 0
    assert fc.length_ok
    assert fc.valid


def test_ten_ellipsis_run_counts_five_punct() -> None:
    """连续 10 个 U+2026（5 对"……"）：P=5（每对计 1，游程内不判连续）。"""
    fc = check_text_rules("…" * 10)
    assert fc.length == 10  # code point 口径（M0 裁决）
    assert fc.punctuation == 5
    # 游程内部成对消耗：整段一个 token → 不报连续标点
    assert fc.no_consecutive_punct
    assert not fc.length_ok  # L=10 < 15


# ======================================================================
# 二、超时/接管/重放/乱序 —— 服务边界（sqlite 组件测试）
# ======================================================================


def test_deadline_config_margin_below_java_takeover_window() -> None:
    """配置护栏：Python deadline=480s 必须严格小于 Java 600s 接管窗口。

    设计 §3.1：480s=8 分钟（<10min 留 Java 接管余量）。该值漂移（如改回 600+）
    会让 Java 三态判定（running 未满 600s 不可接管）与 Python 内部超时竞争。
    """
    cfg = load_initial_review_settings()
    assert cfg.deadline_seconds == 480.0
    assert cfg.deadline_seconds < 600
    assert 600 - cfg.deadline_seconds >= 120  # 接管余量 ≥2 分钟


async def test_replay_after_sweep_returns_failed_no_rerun_no_callback() -> None:
    """迟到重放仅留痕：已被惰性扫描判 FAILED/TIMEOUT 的任务，同键重放
    返回原终态（replayed=True），不再执行、不再发回调（不翻转已接管终态）。"""
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)
    req = make_request()
    fingerprint = service._fingerprint(req)

    now = datetime.now(UTC).replace(tzinfo=None)
    async with sf() as session, session.begin():
        session.add(
            AgentInitialReviewExec(
                task_id="cps-rectify-ISS-001-v1",
                issue_id="ISS-001",
                version_no=1,
                status="RUNNING",
                started_at=now - timedelta(seconds=600),
                deadline_at=now - timedelta(seconds=120),
                fingerprint=fingerprint,
            )
        )

    status = await service.status("cps-rectify-ISS-001-v1")
    assert status["status"] == "FAILED" and status["error_code"] == "TIMEOUT"

    resp = await service.submit(req)  # 同键同参重放
    await drain(pending)
    assert resp["status"] == "FAILED"  # 原终态原样返回
    assert resp["replayed"] is True
    assert len(callback.payloads) == 0  # 不重发回调（Java 已接管/兜底轮询）


async def test_replay_running_row_keeps_original_deadline() -> None:
    """重试不重置计时：RUNNING 行同键重放（Java 重发 C-01）沿用原 deadline_at，
    不会重新加 480s —— 接管窗口以首次提交时刻为锚。"""
    sf = await make_session_factory()
    t0 = datetime(2026, 9, 28, 8, 0, 0)
    service, callback, pending = make_service(sf, clock=lambda: t0 + timedelta(seconds=10))
    req = make_request()
    fingerprint = service._fingerprint(req)

    async with sf() as session, session.begin():
        session.add(
            AgentInitialReviewExec(
                task_id="cps-rectify-ISS-001-v1",
                issue_id="ISS-001",
                version_no=1,
                submission_id="SUB-2026-001",
                status="RUNNING",
                started_at=t0,
                deadline_at=t0 + timedelta(seconds=480),  # 首次提交时刻 +480s
                fingerprint=fingerprint,
                input_snapshot={
                    "reason": VALID_REASON,
                    "short_term_measure": VALID_SHORT,
                    "long_term_measure": VALID_LONG,
                },
            )
        )

    resp = await service.submit(req)  # 时钟已过 10s → remaining=470s
    await drain(pending)
    assert resp["status"] == "COMPLETED"
    # 关键断言：deadline_at 保持首次锚点，未被重放重置
    assert resp["deadline_at"] == (t0 + timedelta(seconds=480)).isoformat()


async def test_completed_replay_sends_no_second_callback() -> None:
    """幂等重放不重发回调：COMPLETED 终态同键重放返回缓存结果，回调只发一次。"""
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    first = await service.submit(make_request())
    await drain(pending)
    second = await service.submit(make_request())  # 同键同参
    await drain(pending)

    assert first["status"] == second["status"] == "COMPLETED"
    assert second["replayed"] is True
    assert len(callback.payloads) == 1


async def test_late_completion_cannot_resurrect_swept_row() -> None:
    """迟到结果仅留痕（执行侧）：已被扫描判 FAILED/TIMEOUT 的任务，
    迟到的执行结果不得翻转终态（乐观锁 WHERE status='RUNNING' 兜底）。"""
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)

    now = datetime.now(UTC).replace(tzinfo=None)
    async with sf() as session, session.begin():
        session.add(
            AgentInitialReviewExec(
                task_id="cps-rectify-LATE-v1",
                issue_id="LATE",
                version_no=1,
                status="RUNNING",
                started_at=now - timedelta(seconds=700),
                deadline_at=now - timedelta(seconds=220),
            )
        )
    status = await service.status("cps-rectify-LATE-v1")
    assert status["status"] == "FAILED"  # 已被接管

    # 迟到的执行完成尝试（_execute 早退：status != RUNNING）
    row = await service._execute("cps-rectify-LATE-v1")
    await drain(pending)
    assert row is not None and row.status == "FAILED"  # 终态不被翻转
    assert len(callback.payloads) == 0  # 不补发回调

    # 乐观锁层再验证：非 RUNNING 行上 mark_terminal 直接败者返回
    finished = await service._finish("cps-rectify-LATE-v1", status="COMPLETED", overall="PASS")
    assert finished is False


async def test_out_of_order_completion_callbacks_carry_own_refs() -> None:
    """乱序回调不串单：v2 先于 v1 终态（乱序完成），两条 C-02 回调各带各的
    task_ref，互不覆盖（Java 侧按幂等键独立对账）。"""
    sf = await make_session_factory()
    service, callback, pending = make_service(sf)
    now = datetime.now(UTC).replace(tzinfo=None)

    for issue, ver in (("ISS-X", 1), ("ISS-X", 2)):
        async with sf() as session, session.begin():
            session.add(
                AgentInitialReviewExec(
                    task_id=f"cps-rectify-ISS-X-v{ver}",
                    issue_id=issue,
                    version_no=ver,
                    status="RUNNING",
                    started_at=now,
                    deadline_at=now + timedelta(seconds=480),
                    input_snapshot={
                        "reason": VALID_REASON,
                        "short_term_measure": VALID_SHORT,
                        "long_term_measure": VALID_LONG,
                    },
                )
            )

    # 乱序执行：先 v2 后 v1
    await service._execute("cps-rectify-ISS-X-v2")
    await service._execute("cps-rectify-ISS-X-v1")
    await drain(pending)

    assert len(callback.payloads) == 2
    refs = [p["task_id"] for p in callback.payloads]
    assert refs == ["cps-rectify-ISS-X-v2", "cps-rectify-ISS-X-v1"]  # 完成序投递
    assert len(set(refs)) == 2  # 各自独立，无串单


async def test_rustfs_http_error_degrades_image_compare_not_fakes() -> None:
    """RustFS 不可达（HTTP 5xx，非配置缺失）：附件获取失败 → DEGRADED
    +「附件获取失败」理由，verdict=SKIPPED 不伪造内容结论。"""
    cfg = RustFSSettings(
        endpoint="http://rustfs.test",
        access_key="ak",
        secret_key="sk",
        bucket="cps-attachments",
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(503, text="unavailable"))
    fetcher = RustFSObjectFetcher(cfg, transport=transport)
    pipe = CheckPipeline(
        make_settings(),
        model_client=FakeModelCheckClient(
            results=[valid_field() for _ in range(3)] + [image_result()]
        ),
        rustfs=fetcher,
    )
    snap = snapshot_of(
        before_attachments=[{"object_key": "before/1.png", "file_name": "1.png"}],
        after_attachments=[{"object_key": "after/1.png", "file_name": "1.png"}],
    )
    mc = await pipe.run(snap, None)
    ic = mc["image_compare"]
    assert ic["implementation_status"] == "DEGRADED"
    assert ic["verdict"] == "SKIPPED"
    assert "附件获取失败" in (ic["reason"] or "")
    assert "503" in (ic["reason"] or "")


@pytest.mark.parametrize("text", ["，" * 20, " " * 20, "　" * 20, "\n" * 20])
def test_extreme_texts_never_raise(text: str) -> None:
    """极端输入安全网：全标点/全空格/全换行文本不抛异常，恒有确定性结论。"""
    fc = check_text_rules(text)
    assert isinstance(fc.valid, bool)
    assert fc.valid is False
