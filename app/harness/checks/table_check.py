"""检查项:表格结构(AI 类)。"""
from __future__ import annotations

from app.harness.base import BaseCheck
from app.harness.checks import llm_check
from app.harness.context import HarnessContext


class TableCheck(BaseCheck):
    name = "table"
    description = "检查文档中的表格是否规范完整"
    sections = ["full"]
    check_type = "ai"

    async def run(self, ctx: HarnessContext):
        doc = self.get_input(ctx)
        prompt = (
            "你是表格检查专家。检查文档中的表格是否规范。\n"
            f"文档内容:\n{doc[:6000]}\n\n"
            "判断:1.表格是否缺行缺列 2.关键单元格是否空缺 3.格式是否规范\n"
            '只输出 JSON: {"pass": true/false, "issues": ["表格X:问题"], '
            '"suggestion": "..."}。无表格时 pass=true。'
        )
        return await llm_check(prompt, name=self.name, severity_on_fail="low")


CHECK = TableCheck()
