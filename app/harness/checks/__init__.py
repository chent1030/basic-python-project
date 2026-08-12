"""检查项包 —— 每个文件一个检查项,自动发现。

加检查项 = 加一个 .py 文件,定义 BaseCheck 子类 + 模块级 CHECK = XxxCheck()。
registry 自动扫描本包,收集所有 CHECK。
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.harness.context import CheckResult


def parse_json_response(text: str) -> dict:
    """从 LLM 输出里提取 JSON(容忍前后文字和 ```json 代码块)。

    所有 AI 类检查项共用这个解析。
    """
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"pass": True, "issues": [], "raw": text[:300]}


async def llm_check(
    prompt: str, *, name: str, severity_on_fail: str = "medium"
) -> CheckResult:
    """AI 检查项的通用执行:调 LLM + 解析 JSON + 构造 CheckResult。

    AI 类检查项调这个即可,不用每次重复写 invoke+parse。
    """
    from app.harness.context import CheckResult
    from app.services.llm import llm

    text = await llm.invoke([{"role": "user", "content": prompt}])
    data = parse_json_response(text)
    passed = data.get("pass", True)
    return CheckResult(
        name=name,
        passed=passed,
        severity=severity_on_fail if not passed else "info",
        issues=data.get("issues", []),
        suggestion=data.get("suggestion", ""),
        detail=text[:500],
        raw=data,
    )
