"""检查项:校对人/批复人校验(规则类)。

纯代码判断:校对人与批复人不得为同一人。
从文档末尾/封面提取人名,与 entity 里的预期人员比对。
"""
from __future__ import annotations

import re

from app.harness.base import BaseCheck
from app.harness.context import CheckResult, HarnessContext


class SignerCheck(BaseCheck):
    name = "signer"
    description = "检查校对人与批复人不得为同一人"
    sections = ["tail", "cover"]
    check_type = "rule"

    async def run(self, ctx: HarnessContext) -> CheckResult:
        text = self.get_input(ctx)

        # 从文档里提取校对人/批复人姓名(中文姓名:2-4字 + "校对人/批复人"前缀)
        reviewer = _extract_name(text, ["校对人", "校对", "编制人", "编制"])
        approver = _extract_name(text, ["批复人", "批复", "审批人", "审批", "批准"])

        # 也从 entity 取预期人员(如有)
        entity_reviewer = (
            ctx.entity.get("reviewer")
            or ctx.entity.get("checker_name")
            or ""
        )
        entity_approver = (
            ctx.entity.get("approver")
            or ctx.entity.get("reviewer_name")
            or ""
        )

        # 优先用文档里提取的,兜底用 entity 的
        r = reviewer or entity_reviewer
        a = approver or entity_approver

        issues: list[str] = []
        if not r or not a:
            issues.append("未能从文档中提取校对人或批复人信息")
        elif r == a:
            issues.append(f"校对人「{r}」与批复人「{a}」为同一人,违反规定")

        passed = len(issues) == 0
        return CheckResult(
            name=self.name,
            passed=passed,
            severity="high" if not passed else "info",
            issues=issues,
            suggestion="校对人与批复人必须为不同人员" if not passed else "",
            detail=f"校对人={r}, 批复人={a}",
        )


def _extract_name(text: str, prefixes: list[str]) -> str:
    """从文本里提取人名(姓名在冒号/空格后,2-4 个中文字符)。"""
    for prefix in prefixes:
        # 匹配 "校对人：张三" / "校对人:张三" / "校对人 张三"
        m = re.search(rf"{prefix}\s*[:：\s]\s*([\u4e00-\u9fa5]{{2,4}})", text)
        if m:
            return m.group(1)
    return ""


CHECK = SignerCheck()
