"""波次 2 真实模型冒烟（手动运行，不进 pytest）。

前置：
- export DASHSCOPE_API_KEY=...（config/agents.yaml qwen provider）
- 本地 PG 无需（本脚本不落数据库）
- 可选：RustFS 已配置（CPS_STORAGE_* env）时补 --object-key 前后照片键做 object_key 模式；
  未给键则退 base64 模式（脚本内自造两张纯色 PNG）。

用法：
    .venv/bin/python scripts/wave2_real_model_smoke.py
    .venv/bin/python scripts/wave2_real_model_smoke.py \
        --before-key cps-attachments/before/1.png --after-key cps-attachments/after/1.png

退出码：0=全部跑通；2=模型不可用（SKIPPED 语义）；1=调用异常（DEGRADED 语义）。
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.projects.initial_review.domain.image_compare import ImageCompareResult
from app.projects.initial_review.domain.text_validity import FieldValidity
from app.projects.initial_review.infrastructure.config import load_initial_review_settings
from app.projects.initial_review.infrastructure.model_client import (
    DashScopeModelClient,
    ModelUnavailableError,
    RustFSObjectFetcher,
)
from app.projects.initial_review.infrastructure.prompts import (
    build_image_compare_prompt,
    build_text_validity_prompt,
)

ISSUE_CONTEXT = "问题标题：接地引下线锈蚀；严重程度：一般；专业：变电"

TEXT_SAMPLES = [
    ("reason", "设备接地引下线锈蚀严重导致接触不良存在安全隐患", "期望 PASS"),
    ("short_term_measure", "已整改", "期望 FAIL（空洞敷衍）"),
    ("long_term_measure", "加强管理", "期望 FAIL（空洞套话）"),
]


def solid_png(rgb: tuple[int, int, int]) -> str:
    """8x8 纯色 PNG 的 base64（无第三方图像库依赖）。"""
    width = height = 8
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


async def main() -> int:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("SKIPPED：未设置 DASHSCOPE_API_KEY（模型不可用，不伪造结果）")
        return 2

    settings = load_initial_review_settings()
    client = DashScopeModelClient(settings.model_check)

    failures = 0

    # ---------------------------------------------------------------- A8 文本
    print("=" * 20, "A8 文本语义有效性（text-validity）", "=" * 20)
    for field, text, expect in TEXT_SAMPLES:
        prompt = build_text_validity_prompt(
            field_name=field, text=text, issue_context=ISSUE_CONTEXT
        )
        try:
            result: FieldValidity = await client.complete_text_json(prompt, FieldValidity)
        except ModelUnavailableError as exc:
            print(f"SKIPPED（{field}）：模型不可用：{exc}")
            return 2
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本需汇总一切故障
            print(f"DEGRADED（{field}）：{type(exc).__name__}: {exc}")
            failures += 1
            continue
        print(f"[{expect}] {field}={text!r}\n  → {result.model_dump_json(ensure_ascii=False)}")

    # ---------------------------------------------------------------- A7 视觉
    print("=" * 20, "A7 照片前后对比（image-compare）", "=" * 20)
    before_args = [a for a in sys.argv[1:] if a.startswith("--before-key")]
    after_args = [a for a in sys.argv[1:] if a.startswith("--after-key")]

    images: list[str] | None = None
    mode = "base64（脚本自造纯色 PNG）"
    if settings.rustfs.unavailable_reason is None and before_args and after_args:
        fetcher = RustFSObjectFetcher(settings.rustfs)
        before_key = before_args[0].split("=", 1)[1]
        after_key = after_args[0].split("=", 1)[1]
        mode = f"object_key（RustFS {settings.rustfs.endpoint}）"
        images = [
            await fetcher.fetch_data_url(before_key),
            await fetcher.fetch_data_url(after_key),
        ]
    elif before_args or after_args:
        print("提示：RustFS 未配置或仅给了一侧 object_key，退 base64 模式")

    if images is None:
        from app.projects.initial_review.infrastructure.model_client import data_url

        images = [
            data_url("image/png", solid_png((120, 40, 40))),
            data_url("image/png", solid_png((40, 120, 40))),
        ]

    prompt = build_image_compare_prompt(issue_context=ISSUE_CONTEXT, readings=None)
    print(f"模式：{mode}")
    try:
        vision: ImageCompareResult = await client.complete_vision_json(
            prompt, images, ImageCompareResult
        )
    except ModelUnavailableError as exc:
        print(f"SKIPPED：模型不可用：{exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"DEGRADED：{type(exc).__name__}: {exc}")
        return 1
    print(f"  → {vision.model_dump_json(ensure_ascii=False)}")

    if failures:
        print(f"\n结果：A8 有 {failures} 个字段异常（DEGRADED）")
        return 1
    print("\n结果：冒烟通过（结构化输出全部符合 schema）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
