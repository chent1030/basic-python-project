"""Pipeline 编排器 —— 顺序执行各 Stage,每 Stage 读写 Context。

Pipeline = 一次业务流程(如文档审核),由多个 Stage 组成。
Stage 之间通过 HarnessContext 传递数据(文本/图片/结构化)。
"""
from __future__ import annotations

import time

from app.core.logging_config import get_logger
from app.harness.base import BaseStage
from app.harness.context import HarnessContext

log = get_logger("app.harness.pipeline")


class Pipeline:
    """编排器:顺序执行 stages。

    用法:
        pipeline = Pipeline([PreprocessStage(), OcrStage(), ...])
        ctx = await pipeline.run(entity, file_url)
    """

    def __init__(self, stages: list[BaseStage]) -> None:
        self.stages = stages

    async def run(
        self, entity: dict, file_url: str, **metadata: object
    ) -> HarnessContext:
        """执行整个 Pipeline,返回填充完成的 Context。"""
        ctx = HarnessContext(entity=entity, file_url=file_url)
        if metadata:
            ctx.metadata.update(metadata)

        log.info(
            "[%s] Pipeline 开始, %d 个 stage, url=%s",
            ctx.pipeline_id, len(self.stages), file_url,
        )

        for stage in self.stages:
            stage_name = stage.name or type(stage).__name__
            started = time.monotonic()
            log.info("[%s] 执行 stage: %s", ctx.pipeline_id, stage_name)
            try:
                ctx = await stage.run(ctx)
                dur = int((time.monotonic() - started) * 1000)
                log.info("[%s] stage %s 完成 (%dms)", ctx.pipeline_id, stage_name, dur)
            except Exception as e:
                log.exception("[%s] stage %s 失败", ctx.pipeline_id, stage_name)
                ctx.metadata.setdefault("errors", []).append(
                    {"stage": stage_name, "error": str(e)}
                )
                raise

        log.info("[%s] Pipeline 完成", ctx.pipeline_id)
        return ctx


__all__ = ["Pipeline"]
