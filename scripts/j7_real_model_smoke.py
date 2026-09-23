"""波次 7 真实模型冒烟（清单⑦ + ASR，手动运行，不进 pytest）。

覆盖三段真实链路（配置可用则真调用、未配则 SKIPPED+原因，**不伪造**）：
- C-04 辅房点检视觉判定：自造小图 PUT 进 RustFS（幂等覆盖同 key）→
  RoomCheckJudgeService.judge() 全链真调用（开关走生产回退链）。
- C-06 语音转写 ASR：合成 1 秒 440Hz 单频 WAV → OpenAiCompatibleAsrClient
  .transcribe() 真调用（注意：合成音频无人声，转写文本可能为空——如实记录，
  本段验证的是链路与网关，不是识别准确率）。
- C-01 初审模型检查：DashScopeModelClient.complete_text_json() 真调用
  （文本语义有效性一例）。

幂等可重跑：RustFS PUT 同 key 覆盖、幂等键固定、ASR/文本调用无副作用。
输出：人读摘要（stdout）+ JSON（默认 scripts/j7_smoke_result.json）。

用法：
    .venv/bin/python scripts/j7_real_model_smoke.py [--json-out PATH]

退出码：0=至少一段真实调用成功且无异常；2=三段全 SKIPPED（前置缺失）；
1=存在调用异常（DEGRADED 语义，详见 JSON errors）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import struct
import sys
import wave
import zlib
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.projects.initial_review.domain.text_validity import FieldValidity
from app.projects.initial_review.infrastructure.config import load_initial_review_settings
from app.projects.initial_review.infrastructure.model_client import (
    DashScopeModelClient,
    ModelUnavailableError,
)
from app.projects.initial_review.infrastructure.prompts import (
    build_text_validity_prompt,
)
from app.projects.room_checks.application.service import RoomCheckJudgeService
from app.projects.room_checks.domain.models import JudgeRequest
from app.projects.room_checks.infrastructure.config import resolve_vision_enabled
from app.projects.speech.infrastructure.asr_client import (
    AsrUnavailableError,
    OpenAiCompatibleAsrClient,
)
from app.projects.speech.infrastructure.config import (
    load_speech_settings,
    resolve_asr_enabled,
)

# 固定幂等键/对象键 → 脚本整体幂等可重跑
SMOKE_KEY_PREFIX = "smoke/j7"
C04_IDEMPOTENCY_KEY = "room-judge-j7-smoke-0001-item-ground-1"
C04_SUBMISSION_ID = "j7-smoke-submission-0001"
ISSUE_CONTEXT = "问题标题：接地引下线锈蚀；严重程度：一般；专业：变电"


def solid_png_bytes(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """size×size 渐变 PNG 字节（stdlib 手工构造，无图像库依赖）。

    注：qwen-vl 系要求边长 >10px（8x8 曾被 400 拒绝，冒烟实录）；渐变而非
    纯色是为了给类型匹配阶段留一点纹理特征。
    """
    width = height = size
    r, g, b = rgb
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            # 水平轻渐变 + 微小棋盘扰动，避免全图单一像素值
            shift = (x * 24) // width
            cell = ((x // 8 + y // 8) % 2) * 6
            row += bytes(
                (max(0, min(255, r + shift + cell)),
                 max(0, min(255, g + shift + cell)),
                 max(0, min(255, b + (shift // 2))))
            )
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def sine_wav_bytes(seconds: float = 1.0, freq: int = 440) -> bytes:
    """16kHz/16bit/单声道正弦 WAV（stdlib wave；供 ASR 链路冒烟）。"""
    rate = 16_000
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        sample = int(8000 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", sample)
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return buf.getvalue()


def _ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def smoke_c04(result: dict) -> None:
    """C-04：上传自造小图 → judge() 真调用（开关走生产回退链）。"""
    section = result["sections"]["C-04"]
    settings = load_initial_review_settings()

    enabled, source = resolve_vision_enabled()
    section["switch"] = {"enabled": enabled, "source": source}
    if not enabled:
        section["status"] = "SKIPPED"
        section["reason"] = f"C-04 视觉判定开关未启用（{source}），不伪造判定"
        return

    rustfs = settings.rustfs
    if rustfs.unavailable_reason:
        section["status"] = "SKIPPED"
        section["reason"] = f"RustFS 未配置：{rustfs.unavailable_reason}"
        return

    # 自造两张纯色 PNG PUT 进 RustFS（同 key 覆盖 = 幂等可重跑）
    from app.projects.weekly_report.infrastructure.config import (
        WeeklyReportRustFSSettings,
    )
    from app.projects.weekly_report.infrastructure.rustfs_uploader import (
        RustFSWeeklyReportUploader,
    )

    uploader = RustFSWeeklyReportUploader(
        WeeklyReportRustFSSettings(
            endpoint=rustfs.endpoint,
            access_key=rustfs.access_key,
            secret_key=rustfs.secret_key,
            bucket=rustfs.bucket,
        )
    )
    before_key, after_key = f"{SMOKE_KEY_PREFIX}/before.png", f"{SMOKE_KEY_PREFIX}/after.png"
    for key, rgb in ((before_key, (150, 130, 90)), (after_key, (90, 140, 90))):
        etag, size = await uploader.put_bytes(
            object_key=key, body=solid_png_bytes(rgb), content_type="image/png"
        )
        section.setdefault("uploaded", []).append(
            {"object_key": key, "etag": etag, "bytes": size}
        )

    svc = RoomCheckJudgeService(settings=settings)  # 生产路径：开关回退链解析
    judged = await svc.judge(
        JudgeRequest(
            idempotency_key=C04_IDEMPOTENCY_KEY,
            submission_id=C04_SUBMISSION_ID,
            item_id="item-ground-1",
            attempt=1,
            item_content="地面无杂物、无积水",
            item_type="地面",
            photo_object_keys=(before_key, after_key),
        )
    )
    section["status"] = judged.status  # JUDGED / SKIPPED
    section["verdict"] = judged.verdict
    section["reason"] = judged.reason
    section["evidence"] = judged.evidence
    section["model_version"] = judged.model_version
    section["stage_trace"] = judged.stage_trace
    if judged.status == "SKIPPED":
        section["note"] = "真实链路完成但结果为 SKIPPED（开关/RustFS/模型端拒判），如实记录"


async def smoke_c06(result: dict) -> None:
    """C-06：合成 WAV → ASR 真调用。"""
    section = result["sections"]["C-06"]
    enabled, source = resolve_asr_enabled()
    cfg = load_speech_settings()
    section["switch"] = {"enabled": enabled, "source": source, "model": cfg.model}
    reason = cfg.unavailable_reason
    if reason:
        section["status"] = "SKIPPED"
        section["reason"] = reason
        return

    audio = sine_wav_bytes()
    section["audio_bytes"] = len(audio)
    try:
        transcription = await OpenAiCompatibleAsrClient(cfg).transcribe(
            audio, audio_format="wav"
        )
    except AsrUnavailableError as exc:
        section["status"] = "SKIPPED"
        section["reason"] = f"ASR 不可用：{exc}"
        return
    # AsrCallError 等技术故障不在此吞掉——交给外层记 DEGRADED
    section["status"] = "TRANSCRIBED"
    section["transcript"] = transcription.text
    section["note"] = (
        "合成单频音无人声，转写为空属预期；本段验证链路/网关/解析，非识别准确率"
    )


async def smoke_c01(result: dict) -> None:
    """C-01：初审文本检查真调用（文本语义有效性一例）。"""
    section = result["sections"]["C-01"]
    settings = load_initial_review_settings()
    section["model_check_enabled"] = settings.model_check.enabled
    if not settings.model_check.enabled:
        section["status"] = "SKIPPED"
        section["reason"] = "initial_review.model_check.enabled=false，不伪造检查"
        return

    from app.services.llm import llm

    await llm.startup()  # DashScopeModelClient 惰性取该单例的 providers
    client = DashScopeModelClient(settings.model_check)
    prompt = build_text_validity_prompt(
        field_name="short_term_measure",
        text="已整改",
        issue_context=ISSUE_CONTEXT,
    )
    try:
        validity: FieldValidity = await client.complete_text_json(prompt, FieldValidity)
    except ModelUnavailableError as exc:
        section["status"] = "SKIPPED"
        section["reason"] = f"模型不可用：{exc}"
        return
    section["status"] = "CHECKED"
    section["result"] = validity.model_dump()


async def main() -> int:
    parser = argparse.ArgumentParser(description="波次 7 真实模型冒烟")
    parser.add_argument(
        "--json-out",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "j7_smoke_result.json"),
        help="结构化 JSON 输出路径（默认 scripts/j7_smoke_result.json，覆盖写）",
    )
    args = parser.parse_args()

    result: dict = {
        "wave": 7,
        "script": "j7_real_model_smoke",
        "ran_at": _ts(),
        "sections": {"C-04": {}, "C-06": {}, "C-01": {}},
        "errors": {},
    }

    # llm 单例先就绪（C-04 judge 内部 DashScopeModelClient 惰性取其 providers；
    # 未 startup 时 providers 为空 → 全部误判「未配置」）
    from app.services.llm import llm

    await llm.startup()

    for name, fn in (("C-04", smoke_c04), ("C-06", smoke_c06), ("C-01", smoke_c01)):
        try:
            await fn(result)
        except Exception as exc:  # noqa: BLE001 - 冒烟需汇总一切故障并继续下一段
            result["sections"][name]["status"] = "DEGRADED"
            result["errors"][name] = f"{type(exc).__name__}: {exc}"

    # ---- 人读摘要 ----
    print("=" * 24, "波次 7 真实模型冒烟", "=" * 24)
    for name, sec in result["sections"].items():
        line = f"[{name}] {sec.get('status')}"
        if sec.get("verdict"):
            line += f" verdict={sec['verdict']}"
        if sec.get("transcript") is not None:
            line += f" transcript={sec['transcript']!r}"
        if sec.get("reason"):
            line += f"（{sec['reason']}）"
        print(line)
    if result["errors"]:
        for name, err in result["errors"].items():
            print(f"!! {name} DEGRADED：{err}")

    statuses = [sec.get("status") for sec in result["sections"].values()]
    exit_code = 1 if result["errors"] or "DEGRADED" in statuses else (
        2 if all(s == "SKIPPED" for s in statuses) else 0
    )
    result["exit_code"] = exit_code
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\nJSON → {args.json_out}；退出码 {exit_code}"
          "（0=有真实判定；2=全 SKIPPED；1=有异常）")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
