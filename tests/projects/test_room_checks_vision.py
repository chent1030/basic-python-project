"""B6 视觉模型客户端测试（qwen-vl-plus multi-modal round-trip + prompt 注入）。

覆盖目标：
1. mock VisionModelClient qwen-vl-plus round-trip（VisionCallResult 形态）
2. PromptBuilder 三级 prompt 注入 expected_keywords / expected_match_type
3. PromptBuilder 末尾强制 _JSON_ONLY_RULE
4. PromptBuilder evidence_strength 模板正确性
5. VisionModelClient 协议 typing 检查（Protocol 满足）
"""

from __future__ import annotations

import pytest

from app.projects.room_checks.infrastructure.prompt_builder import (
    PROMPT_VERSION_CONTENT,
    PROMPT_VERSION_EVIDENCE,
    PROMPT_VERSION_TYPE_MATCH,
    PromptBuilder,
)
from app.projects.room_checks.infrastructure.vision_client import (
    FakeVisionModelClient,
    VisionCallResult,
    VisionModelClient,
)
from tests.projects.conftest import make_evidence


# -- VisionModelClient round-trip --------------------------------------------
@pytest.mark.asyncio
async def test_fake_vision_client_round_trip() -> None:
    """FakeVisionModelClient 按脚本返回，验证多模态调用形状。"""
    expected = VisionCallResult(
        raw_text='{"matched": true}',
        parsed={"matched": True},
        model="qwen-vl-plus",
    )
    client = FakeVisionModelClient(results=[expected])
    assert isinstance(client, VisionModelClient)  # Protocol 满足

    out = await client.complete_vision(
        "test prompt", image_data_urls=("data:image/jpeg;base64,/9j/test",)
    )
    assert out.raw_text == '{"matched": true}'
    assert out.parsed == {"matched": True}
    assert out.model == "qwen-vl-plus"
    # 记录了调用上下文（便于断言 prompt 注入）
    assert len(client.calls) == 1
    assert client.calls[0]["prompt"] == "test prompt"


@pytest.mark.asyncio
async def test_fake_vision_client_records_injected_prompt_and_images() -> None:
    """注入 prompt + image_data_urls 在 calls 中可观测。"""
    client = FakeVisionModelClient(
        results=[VisionCallResult(raw_text="{}", parsed={}, model="qwen-vl-plus")]
    )
    await client.complete_vision(
        "prompt-XYZ", image_data_urls=("data:url1", "data:url2")
    )
    assert client.calls[0]["prompt"] == "prompt-XYZ"
    assert client.calls[0]["images"] == ["data:url1", "data:url2"]


@pytest.mark.asyncio
async def test_fake_vision_client_propagates_model_unavailable() -> None:
    """预设 ``ModelUnavailableError`` 时直接抛出。"""
    from app.projects.initial_review.infrastructure.model_client import (
        ModelUnavailableError,
    )

    client = FakeVisionModelClient(unavailable="provider not configured")
    with pytest.raises(ModelUnavailableError):
        await client.complete_vision("p", image_data_urls=("data:img",))


# -- PromptBuilder 注入测试 --------------------------------------------------
def test_type_match_prompt_injects_expected_match_type() -> None:
    """type-match prompt 显式包含 expected_match_type 字段。"""
    builder = PromptBuilder()
    evidence = make_evidence(expected_match_type="配电柜")
    prompt = builder.build_type_match_prompt(evidence)
    assert "配电柜" in prompt
    assert "期望照片主体" in prompt or "期望" in prompt


def test_type_match_prompt_injects_observed_meta() -> None:
    """照片元数据（EXIF/filename）注入 type-match prompt。"""
    builder = PromptBuilder()
    evidence = make_evidence()
    prompt = builder.build_type_match_prompt(
        evidence, observed_meta={"filename": "ground.jpg", "size_kb": "234"}
    )
    assert "filename: ground.jpg" in prompt
    assert "size_kb: 234" in prompt
    assert "照片元数据" in prompt


def test_content_prompt_injects_expected_keywords() -> None:
    """content prompt 注入 expected_keywords 列表。"""
    builder = PromptBuilder()
    evidence = make_evidence(
        expected_keywords=("无杂物", "无积水", "无破损")
    )
    prompt = builder.build_content_prompt(evidence)
    assert "无杂物" in prompt
    assert "无积水" in prompt
    assert "无破损" in prompt
    # 末尾强制 JSON-only 约束（与 J 线遗留 prompt v2 同模式）
    assert "JSON" in prompt and "{" in prompt and "}" in prompt


def test_content_prompt_skips_keywords_section_when_empty() -> None:
    """expected_keywords 为空时跳过关键词核对段（避免无谓失败）。"""
    builder = PromptBuilder()
    evidence = make_evidence(expected_keywords=())
    prompt = builder.build_content_prompt(evidence)
    # 「期望关键物证」作为 section 标题不应出现（rules 段含同名短语属正常）
    assert "【期望关键物证】" not in prompt
    assert "- 无杂物" not in prompt
    assert "- 无积水" not in prompt


def test_evidence_prompt_includes_content_match_summary() -> None:
    """evidence prompt 显式汇总二级结论 + matched/missing 列表。"""
    builder = PromptBuilder()
    evidence = make_evidence()
    prompt = builder.build_evidence_prompt(
        evidence,
        content_match=True,
        matched_keywords=("无杂物",),
        missing_keywords=("无积水",),
    )
    assert "前置判定" in prompt
    assert "content_match=true" in prompt
    assert "matched=" in prompt
    assert "missing=" in prompt


def test_prompt_versions_anchored() -> None:
    """版本常量锚点稳定（监控 / 审计用）。"""
    assert PROMPT_VERSION_TYPE_MATCH == "room-check/type-match/qwen-vl-plus@1"
    assert PROMPT_VERSION_CONTENT == "room-check/content/qwen-vl-plus@1"
    assert PROMPT_VERSION_EVIDENCE == "room-check/evidence/qwen-vl-plus@1"