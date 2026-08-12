"""检查项:大小标题完整性(AI 类)。"""
from __future__ import annotations

from app.harness.base import BaseCheck
from app.harness.checks import llm_check
from app.harness.context import HarnessContext


class HeadingCheck(BaseCheck):
    name = "heading"
    description = "检查大小标题是否完整、层级是否合理"
    sections = ["full"]
    check_type = "ai"

    async def run(self, ctx: HarnessContext):
        doc = self.get_input(ctx)
        prompt = (
            "你是文档结构检查专家。检查文档标题是否完整。\n"
            f"文档内容:\n{doc[:6000]}\n\n"
            "判断:1.是否缺少必要标题 2.标题层级是否合理 3.编号是否连续\n"
            '只输出 JSON: {"pass": true/false, "issues": ["缺失标题:XXX"], '
            '"suggestion": "..."}'
        )
        return await llm_check(prompt, name=self.name, severity_on_fail="medium")


CHECK = HeadingCheck()
