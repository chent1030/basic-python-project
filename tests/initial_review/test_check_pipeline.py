"""波次 2 检查管线（CheckPipeline）单测：fake LLM 注入，不触网不触真模型。

覆盖：
- A8：IMPLEMENTED / 模型不可用 SKIPPED / 技术失败与单字段预算超时 DEGRADED / 空字段跳过；
- A7：base64 直传 / object_key 经 RustFS（httpx.MockTransport 模拟 S3）/ 单侧缺照片 SKIPPED /
  重放执行无 base64 原文 DEGRADED / 读数不一致不弱于 WARN；
- A6：历史合并（issue_snapshot.history_submissions + DB 装载器去重）。
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses

import httpx
import pytest

from app.projects.initial_review.application.check_pipeline import CheckPipeline
from app.projects.initial_review.domain.image_compare import ImageCompareResult
from app.projects.initial_review.domain.models import AttachmentRef
from app.projects.initial_review.domain.text_validity import FieldValidity
from app.projects.initial_review.domain.verdicts import aggregate_overall
from app.projects.initial_review.infrastructure.model_client import (
    ModelCallError,
    RustFSObjectFetcher,
    extract_json_object,
)
from tests.initial_review.conftest import (
    VALID_LONG,
    VALID_REASON,
    VALID_SHORT,
    make_settings,
)


def settings_with(**mc_over):
    """冻结配置的不可变覆写（预算/张数上限等可配项）。"""
    settings = make_settings()
    return dataclasses.replace(
        settings, model_check=dataclasses.replace(settings.model_check, **mc_over)
    )


def snapshot_of(**over) -> dict:
    snap = {
        "issue_id": "ISS-001",
        "submission_id": "SUB-1",
        "version_no": 2,
        "reason": VALID_REASON,
        "short_term_measure": VALID_SHORT,
        "long_term_measure": VALID_LONG,
        "before_attachments": [],
        "after_attachments": [],
        "issue_snapshot": None,
    }
    snap.update(over)
    return snap


def valid_field(verdict: str = "PASS") -> FieldValidity:
    return FieldValidity(
        verdict=verdict, reason="内容具体与问题相关", confidence=0.9, problem_fragment=None
    )


def image_result(
    verdict: str = "PASS",
    *,
    readings_observed: str | None = None,
    readings_match: bool | None = None,
) -> ImageCompareResult:
    return ImageCompareResult(
        verdict=verdict,
        reason="后照可见更换后的新接地线",
        confidence=0.85,
        evidence_before=["前照第1张锈蚀"],
        evidence_after=["后照第1张新线"],
        readings_observed=readings_observed,
        readings_match=readings_match,
    )


PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d4944415478da63f8cfc0f01f0005000105ab5e2f"
        "470000000049454e44ae426082"
    )
).decode("ascii")


def b64_attachment(content: str = PNG_1PX, name: str = "a.png") -> AttachmentRef:
    return AttachmentRef(
        attachment_id="att-1", file_name=name, content_base64=content
    )


def key_attachment(key: str = "before/1.png") -> AttachmentRef:
    return AttachmentRef(attachment_id="att-k1", file_name="1.png", object_key=key)


class Req:
    """最小 request 替身：pipeline 只读 before/after_attachments 属性。"""

    def __init__(self, before: list | None, after: list | None) -> None:
        self.before_attachments = before or []
        self.after_attachments = after or []


class NullClient:
    """文本调用恒 PASS；视觉调用出现即失败（A7 专项断言用）。"""

    async def complete_text_json(self, prompt, schema):
        return valid_field()

    async def complete_vision_json(self, prompt, urls, schema):
        raise AssertionError("不应触视觉调用")


def rustfs_mock(payload: bytes = b"pngbytes") -> RustFSObjectFetcher:
    settings = make_settings()
    from app.projects.initial_review.infrastructure.config import RustFSSettings

    cfg = RustFSSettings(
        endpoint="http://rustfs.test",
        access_key="ak",
        secret_key="sk",
        bucket="cps-attachments",
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=payload)
    )
    assert cfg.unavailable_reason is None
    assert settings is not None
    return RustFSObjectFetcher(cfg, transport=transport)


# ------------------------------------------------------------------ extract_json


@pytest.mark.parametrize(
    ("text", "has_verdict"),
    [
        ('{"verdict": "PASS"}', True),
        ('```json\n{"verdict": "PASS"}\n```', True),
        ('前置说明 {"verdict": "PASS"} 后缀', True),
        ("完全不是 JSON", False),
        ('{"verdict": 1}', True),  # 提取层只管 JSON 形；类型校验由 pydantic 层负责
        # ---- 波次 8（J 线转交 #2）：真实失败样本形态的容错降级 ----
        # 栅栏后仍有尾注（模型爱在代码块外加一句「以上是结果」）
        ('```json\n{"verdict": "PASS"}\n```\n以上 JSON 即判定结果。', True),
        # 栅栏语言标记大写
        ('```JSON\n{"verdict": "PASS"}\n```', True),
        # 裸栅栏（无语言标记）
        ('```\n{"verdict": "PASS"}\n```', True),
        # 前后都有大段说明文字（平衡扫描取首个完整对象）
        (
            "好的，我来分析。首先看整改原因：\n第一点内容具体。\n\n"
            '{"verdict": "PASS", "reason": "内容具体相关"}\n\n'
            "综上，该字段判定通过。",
            True,
        ),
        # 键值用单引号（部分模型/温度漂移时出现）
        ("{'verdict': 'PASS', 'reason': '内容具体'}", True),
        # 单引号 + 尾逗号叠加
        ("{'verdict': 'PASS', 'reason': '内容具体',}", True),
        # JSON 对象后紧跟另一个代码块说明（取首个平衡对象，不吃到杂讯）
        ('{"verdict": "WARN"}\n```\n补充说明文字\n```', True),
        # 值内含花括号/引号转义（字符串感知扫描不被内层 } 骗到）
        ('说明 {"verdict": "FAIL", "reason": "输出形如 {x}"} 尾注', True),
        # 只有数组没有对象 → 仍失败（调用方回喂重试）
        ('["verdict", "PASS"]', False),
        # 对象被截断（deadline 到点截断输出）→ 失败
        ('{"verdict": "PASS", "reason": "截', False),
    ],
)
def test_extract_json_object(text: str, has_verdict: bool) -> None:
    if has_verdict:
        assert extract_json_object(text).get("verdict") is not None
    else:
        with pytest.raises(ValueError):
            extract_json_object(text)


# ------------------------------------------------------------------------ A8


async def test_text_validity_implemented_via_fake_client() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)])
    pipe = CheckPipeline(make_settings(), model_client=fake)
    mc = await pipe.run(snapshot_of(), None)
    tv = mc["text_validity"]
    assert tv["implementation_status"] == "IMPLEMENTED"
    assert all(f["status"] == "IMPLEMENTED" for f in tv["fields"].values())
    assert all(f["verdict"] == "PASS" for f in tv["fields"].values())
    assert tv["prompt_version"] == "text-validity/qwen-plus@2"
    assert len(fake.calls) == 3 and all(c["kind"] == "text" for c in fake.calls)


async def test_text_validity_model_unavailable_marks_skipped() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(unavailable="未配置任何 LLM provider（llm.providers 为空）")
    pipe = CheckPipeline(make_settings(), model_client=fake)
    mc = await pipe.run(snapshot_of(), None)
    tv = mc["text_validity"]
    assert tv["implementation_status"] == "SKIPPED"
    for f in tv["fields"].values():
        assert f["verdict"] == "SKIPPED"
        assert "模型不可用" in f["reason"]
        assert "不伪造" not in f["reason"]  # SKIPPED 语义：前置缺失而非故障


async def test_text_validity_call_error_degrades_not_fakes() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(
        results=[ModelCallError("模型输出两次无效（FieldValidity）") for _ in range(3)]
    )
    pipe = CheckPipeline(make_settings(), model_client=fake)
    mc = await pipe.run(snapshot_of(), None)
    fields = mc["text_validity"]["fields"]
    assert fields["reason"]["status"] == "DEGRADED"
    assert fields["reason"]["verdict"] == "SKIPPED"
    assert "执行失败" in fields["reason"]["reason"]
    assert mc["text_validity"]["implementation_status"] == "DEGRADED"
    # 其余字段照常执行（单字段故障不放大）
    assert fields["short_term_measure"]["status"] == "DEGRADED"  # fake 耗尽后同样降级路径
    assert fields["long_term_measure"]["status"] == "DEGRADED"


async def test_text_validity_field_budget_timeout_degrades() -> None:
    class StallClient:
        async def complete_text_json(self, prompt, schema):
            await asyncio.sleep(5)

        async def complete_vision_json(self, prompt, urls, schema):  # pragma: no cover
            raise AssertionError("不应触视觉调用")

    settings = settings_with(field_budget_seconds=0.05)  # 可配预算
    pipe = CheckPipeline(settings, model_client=StallClient())
    mc = await pipe.run(snapshot_of(), None)
    assert mc["text_validity"]["implementation_status"] == "DEGRADED"
    for f in mc["text_validity"]["fields"].values():
        assert f["status"] == "DEGRADED"
        assert "预算超时" in f["reason"]


async def test_empty_field_skips_semantic_call() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)])
    pipe = CheckPipeline(make_settings(), model_client=fake)
    snap = snapshot_of(reason="   ")
    mc = await pipe.run(snap, None)
    reason_field = mc["text_validity"]["fields"]["reason"]
    assert reason_field["status"] == "SKIPPED"
    assert "字段为空" in reason_field["reason"]
    assert len(fake.calls) == 2  # 空字段不烧模型调用


async def test_pipeline_without_model_client_skips_a7_a8() -> None:
    pipe = CheckPipeline(make_settings())
    mc = await pipe.run(snapshot_of(), None)
    assert mc["text_validity"]["implementation_status"] == "SKIPPED"
    assert mc["image_compare"]["implementation_status"] == "SKIPPED"
    # A6 确定性检查不受模型装配影响
    assert mc["measure_similarity"]["implementation_status"] == "IMPLEMENTED"


# ------------------------------------------------------------------------ A7


async def test_image_compare_no_attachments_skips() -> None:
    pipe = CheckPipeline(make_settings(), model_client=NullClient())
    mc = await pipe.run(snapshot_of(), None)
    ic = mc["image_compare"]
    assert ic["verdict"] == "SKIPPED"
    assert "未提交" in ic["reason"]


async def test_image_compare_one_side_missing_skips() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)])
    pipe = CheckPipeline(make_settings(), model_client=fake)
    snap = snapshot_of(
        before_attachments=[
            {"attachment_id": "att-k1", "object_key": "before/1.png", "file_name": "1.png"}
        ],
        after_attachments=[],
    )
    mc = await pipe.run(snap, Req([key_attachment()], []))
    assert mc["image_compare"]["verdict"] == "SKIPPED"
    assert "一侧" in mc["image_compare"]["reason"]
    assert all(c["kind"] == "text" for c in fake.calls)  # 未触视觉调用


async def test_image_compare_base64_mode_end_to_end() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)] + [image_result()])
    pipe = CheckPipeline(make_settings(), model_client=fake)
    before, after = b64_attachment(), b64_attachment(PNG_1PX, "b.png")
    snap = snapshot_of(
        before_attachments=[
            {
                "attachment_id": "att-1", "file_name": "a.png",
                "content_base64_sha256": "ab12", "content_base64_length": 100,
            }
        ],
        after_attachments=[
            {
                "attachment_id": "att-2", "file_name": "b.png",
                "content_base64_sha256": "cd34", "content_base64_length": 100,
            }
        ],
    )
    mc = await pipe.run(snap, Req([before], [after]))
    ic = mc["image_compare"]
    assert ic["implementation_status"] == "IMPLEMENTED"
    assert ic["verdict"] == "PASS"
    assert ic["evidence_refs"] == ["att-1", "att-2"]
    # 送检 data URL 前后拼接，MIME 由文件名推断
    call = fake.calls[-1]
    assert call["kind"] == "vision"
    assert call["images"][0].startswith("data:image/png;base64,")
    assert len(call["images"]) == 2


async def test_image_compare_object_key_mode_fetches_from_rustfs() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)] + [image_result()])
    fetcher = rustfs_mock()
    pipe = CheckPipeline(make_settings(), model_client=fake, rustfs=fetcher)
    snap = snapshot_of(
        before_attachments=[
            {"attachment_id": "att-k1", "object_key": "before/1.png", "file_name": "1.png"}
        ],
        after_attachments=[
            {"attachment_id": "att-k2", "object_key": "after/1.png", "file_name": "1.png"}
        ],
    )
    mc = await pipe.run(snap, None)  # object_key 模式无需 request 原文
    ic = mc["image_compare"]
    assert ic["implementation_status"] == "IMPLEMENTED"
    assert ic["evidence_refs"] == ["before/1.png", "after/1.png"]
    assert fake.calls[-1]["images"][0].startswith("data:image/png;base64,")


async def test_image_compare_rustfs_unavailable_skips() -> None:
    """RustFS 未配置（object_key 模式前置缺失）→ SKIPPED 不伪造。"""
    from app.projects.initial_review.infrastructure.config import RustFSSettings
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
        RustFSObjectFetcher,
    )

    fetcher = RustFSObjectFetcher(RustFSSettings("", "", "", ""))
    pipe = CheckPipeline(
        make_settings(),
        model_client=FakeModelCheckClient(results=[valid_field() for _ in range(3)]),
        rustfs=fetcher,
    )
    snap = snapshot_of(
        before_attachments=[{"object_key": "before/1.png"}],
        after_attachments=[{"object_key": "after/1.png"}],
    )
    mc = await pipe.run(snap, None)
    ic = mc["image_compare"]
    assert ic["verdict"] == "SKIPPED"
    assert "配置不完整" in ic["reason"]


async def test_image_compare_base64_replay_without_original_degrades() -> None:
    """重放执行（request=None）拿不到 base64 原文 → DEGRADED，不伪造结论。"""
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    pipe = CheckPipeline(
        make_settings(),
        model_client=FakeModelCheckClient(results=[valid_field() for _ in range(3)]),
    )
    snap = snapshot_of(
        before_attachments=[
            {"attachment_id": "att-1", "content_base64_sha256": "ab12", "file_name": "a.png"}
        ],
        after_attachments=[
            {"attachment_id": "att-2", "content_base64_sha256": "cd34", "file_name": "b.png"}
        ],
    )
    mc = await pipe.run(snap, None)
    ic = mc["image_compare"]
    assert ic["implementation_status"] == "DEGRADED"
    assert "原文不可得" in ic["reason"]


async def test_image_compare_readings_mismatch_escalates_to_warn() -> None:
    """提交读数与照片不一致：PASS 也不得低于 WARN（照片与登记值矛盾）。"""
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    fake = FakeModelCheckClient(
        results=[valid_field() for _ in range(3)]
        + [image_result(readings_observed="0.4Ω", readings_match=False)]
    )
    pipe = CheckPipeline(make_settings(), model_client=fake, rustfs=rustfs_mock())
    snap = snapshot_of(
        before_attachments=[
            {"attachment_id": "att-k1", "object_key": "before/1.png", "file_name": "1.png"}
        ],
        after_attachments=[
            {"attachment_id": "att-k2", "object_key": "after/1.png", "file_name": "1.png"}
        ],
        issue_snapshot={"readings": [{"name": "接地电阻", "value": "≤0.8Ω"}]},
    )
    mc = await pipe.run(
        snap, Req([key_attachment("before/1.png")], [key_attachment("after/1.png")])
    )
    ic = mc["image_compare"]
    assert ic["verdict"] == "WARN"
    assert ic["readings"]["provided"] is True
    assert ic["readings"]["match"] is False
    assert "不一致" in ic["readings"]["note"]
    # 读数核对指令并入了同一次视觉调用（不另发请求）
    assert "读数核对" in fake.calls[-1]["prompt"]
    vision_calls = [c for c in fake.calls if c["kind"] == "vision"]
    assert len(vision_calls) == 1


async def test_image_compare_truncates_images_per_side() -> None:
    from app.projects.initial_review.infrastructure.model_client import (
        FakeModelCheckClient,
    )

    settings = settings_with(max_images_per_side=2)
    fake = FakeModelCheckClient(results=[valid_field() for _ in range(3)] + [image_result()])
    fetcher = rustfs_mock()
    pipe = CheckPipeline(settings, model_client=fake, rustfs=fetcher)
    snap = snapshot_of(
        before_attachments=[{"object_key": f"before/{i}.png"} for i in range(4)],
        after_attachments=[{"object_key": f"after/{i}.png"} for i in range(4)],
    )
    mc = await pipe.run(snap, None)
    assert len(fake.calls[-1]["images"]) == 4  # 每侧 2 张
    assert "每侧仅取前 2 张" in mc["image_compare"]["reason"]


# ------------------------------------------------------------------------ A6


async def test_history_merges_snapshot_and_loader_with_dedupe() -> None:
    async def loader(issue_id: str, version_no: int) -> list[dict]:
        assert (issue_id, version_no) == ("ISS-001", 2)
        return [
            {  # 与快照重复（同版本同文本）→ 去重
                "reason": "历史版本的整改原因说明文字内容表述",
                "short_term_measure": "历史版本短期措施更换绝缘子试验合格",
                "long_term_measure": "历史版本长期措施红外测温更新台账",
                "version_no": 1,
            },
            {  # DB 独有版本 → 保留
                "reason": "第二版历史原因完全不同表述另一段文字",
                "short_term_measure": "第二版历史短期措施完全不同",
                "long_term_measure": "第二版历史长期措施完全不同",
                "version_no": 1,
            },
        ]

    pipe = CheckPipeline(make_settings(), history_loader=loader)
    snap = snapshot_of(
        issue_snapshot={
            "history_submissions": [
                {
                    "reason": "历史版本的整改原因说明文字内容表述",
                    "short_term_measure": "历史版本短期措施更换绝缘子试验合格",
                    "long_term_measure": "历史版本长期措施红外测温更新台账",
                    "version_no": 1,
                }
            ]
        }
    )
    mc = await pipe.run(snap, None)
    history = mc["measure_similarity"]["history"]
    assert history["samples"] == 2  # 去重后 2 条
    assert history["verdict"] == "PASS"


async def test_history_loader_failure_does_not_block_pipeline() -> None:
    async def broken_loader(issue_id: str, version_no: int) -> list[dict]:
        raise RuntimeError("db down")

    pipe = CheckPipeline(make_settings(), history_loader=broken_loader)
    mc = await pipe.run(snapshot_of(), None)
    assert mc["measure_similarity"]["implementation_status"] == "IMPLEMENTED"
    assert mc["measure_similarity"]["history"]["verdict"] == "SKIPPED"


async def test_history_resubmission_via_pipeline_marks_problem() -> None:
    """v2 原样重提 v1 文本：管线口径下 overall=PROBLEM（端到端聚合）。"""
    async def loader(issue_id: str, version_no: int) -> list[dict]:
        return [
            {
                "reason": VALID_REASON,
                "short_term_measure": VALID_SHORT,
                "long_term_measure": VALID_LONG,
                "version_no": 1,
            }
        ]

    pipe = CheckPipeline(make_settings(), history_loader=loader)
    mc = await pipe.run(snapshot_of(), None)
    overall, _ = aggregate_overall({}, mc)
    assert mc["measure_similarity"]["verdict"] == "FAIL"
    assert overall == "PROBLEM"
