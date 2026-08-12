"""检查项:公章检查(规则类)。

纯代码判断,不调 LLM。
检查:1.文档末尾是否有红色公章描述 2.公章文字是否与供应商名称一致。
基准数据从 ctx.entity 取(vendorName/supplier_name 等)。
"""
from __future__ import annotations

from app.harness.base import BaseCheck
from app.harness.context import CheckResult, HarnessContext


class SealCheck(BaseCheck):
    name = "seal"
    description = "检查公章:红色公章存在 + 公章名称与供应商一致"
    sections = ["tail"]
    check_type = "rule"

    async def run(self, ctx: HarnessContext) -> CheckResult:
        tail = self.get_input(ctx)

        issues: list[str] = []

        # 1. 检查是否有红色公章描述(OCR 文本里通常会有"公章/印章/红色"等描述)
        seal_keywords = ["公章", "印章", "盖章", "红色", "专用章"]
        has_seal = any(kw in tail for kw in seal_keywords)
        if not has_seal:
            issues.append("文档末尾未检测到公章相关内容")

        # 2. 公章文字 vs 供应商名(从 entity 取供应商名)
        # entity 里可能是 vendorName / supplier_name / companyName 等
        supplier = (
            ctx.entity.get("vendorName")
            or ctx.entity.get("supplier_name")
            or ctx.entity.get("companyName")
            or ""
        )
        if supplier and has_seal:
            # 从末尾内容里找公章文字(通常含公司名)
            if supplier not in tail:
                issues.append(
                    f"公章文字与供应商名称「{supplier}」不一致(末尾未找到匹配)"
                )

        passed = len(issues) == 0
        return CheckResult(
            name=self.name,
            passed=passed,
            severity="high" if not passed else "info",
            issues=issues,
            suggestion="请确认底部加盖红色公章且名称与供应商一致" if not passed else "",
            detail=f"供应商={supplier}, 末尾含公章描述={has_seal}",
        )


CHECK = SealCheck()
