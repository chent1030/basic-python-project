"""检查项:错别字(AI 类)。一文件一个检查项,自动发现。"""
from __future__ import annotations

from app.harness.base import BaseCheck
from app.harness.checks import llm_check
from app.harness.context import HarnessContext


class TypoCheck(BaseCheck):
    name = "typo"
    description = "检查文档中的错别字"
    sections = ["full"]
    check_type = "ai"

    async def run(self, ctx: HarnessContext):
        doc = self.get_input(ctx)
        prompt = (
            "你是文字校对专家。检查以下文档中的错别字。\n"
            f"文档内容:\n{doc[:6000]}\n\n"
            '只输出 JSON: {"pass": true/false, "issues": ["位置:错误词->正确词"], '
            '"suggestion": "总结"}。没有错别字时 pass=true。'
        )
        return await llm_check(prompt, name=self.name, severity_on_fail="medium")


CHECK = TypoCheck()
