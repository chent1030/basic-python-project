"""检查项:封面等级规范(AI 类)。"""
from __future__ import annotations

import json

from app.harness.base import BaseCheck
from app.harness.checks import llm_check
from app.harness.context import HarnessContext


class CoverCheck(BaseCheck):
    name = "cover"
    description = "检查封面等级信息是否规范"
    sections = ["cover"]
    check_type = "ai"

    async def run(self, ctx: HarnessContext):
        doc = self.get_input(ctx)
        entity_text = json.dumps(ctx.entity, ensure_ascii=False)
        prompt = (
            "你是封面信息规范检查专家。检查封面等级信息是否规范。\n"
            f"业务数据(比对基准):{entity_text}\n\n"
            f"封面内容:\n{doc[:4000]}\n\n"
            "判断:1.等级信息是否完整 2.格式是否符合规范 3.关键信息是否与业务数据一致\n"
            '只输出 JSON: {"pass": true/false, "issues": ["问题"], "suggestion": "..."}'
        )
        return await llm_check(prompt, name=self.name, severity_on_fail="high")


CHECK = CoverCheck()
