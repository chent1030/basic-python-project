"""文档审核规则工具集 —— 纯代码判断(不走 LLM),快且准、省钱。

这些工具处理「确定性判断」:人名比对、名称一致性等。
模糊判断(错别字、标题完整性、表格结构)交给 AI checker agent。

规则类 checker agent(rule_seal_checker / rule_signer_checker)调这些工具拿结果。
"""
from __future__ import annotations

from app.ai.tools import tool


@tool("check_name_conflict")
async def check_name_conflict(name1: str, name2: str) -> str:
    """检查两个人名是否为同一人(校对人 vs 批复人不得相同)。

    Args:
        name1: 第一个人名(如校对人)。
        name2: 第二个人名(如批复人)。
    """
    n1 = name1.strip().replace(" ", "")
    n2 = name2.strip().replace(" ", "")
    if not n1 or not n2:
        return '{"pass": true, "detail": "存在未填写的人员,跳过冲突检查"}'
    conflict = n1 == n2
    if conflict:
        return (
            f'{{"pass": false, "detail": "校对人「{name1}」与批复人「{name2}」'
            f'为同一人,违反规定"}}'
        )
    return (
        f'{{"pass": true, "detail": "校对人「{name1}」与批复人「{name2}」不为同一人"}}'
    )


@tool("check_seal_supplier")
async def check_seal_supplier(seal_text: str, supplier_name: str) -> str:
    """检查公章文字是否与供应商名称一致。

    Args:
        seal_text: OCR 识别出的公章文字。
        supplier_name: entity 里的供应商名称(比对基准)。
    """
    s = seal_text.strip().replace(" ", "")
    sup = supplier_name.strip().replace(" ", "")
    if not s:
        return '{"pass": false, "detail": "未检测到公章文字"}'
    if not sup:
        return '{"pass": true, "detail": "未提供供应商名称,跳过比对"}'
    # 公章文字常含前后缀(如"XXX有限公司公章"),做包含判断
    match = sup in s or s in sup
    if match:
        return (
            f'{{"pass": true, "detail": "公章文字「{seal_text}」'
            f'与供应商「{supplier_name}」一致"}}'
        )
    return (
        f'{{"pass": false, "detail": "公章文字「{seal_text}」'
        f'与供应商「{supplier_name}」不一致"}}'
    )


@tool("check_red_seal")
async def check_red_seal(has_red_region: bool, bottom_area: bool) -> str:
    """检查文档底部是否有红色公章区域(基于 OCR/图像分析的结果判断)。

    Args:
        has_red_region: 是否检测到红色区域(True/False,字符串也行)。
        bottom_area: 红色区域是否在文档底部位置。
    """
    red = str(has_red_region).strip().lower() in ("true", "1", "yes", "是")
    bottom = str(bottom_area).strip().lower() in ("true", "1", "yes", "是")
    if not red:
        return '{"pass": false, "detail": "未检测到红色公章"}'
    if not bottom:
        return '{"pass": false, "detail": "检测到红色区域但不在底部位置"}'
    return '{"pass": true, "detail": "底部检测到红色公章"}'


@tool("check_required_fields")
async def check_required_fields(doc_content: str, fields: str) -> str:
    """检查文档是否包含所有必填字段(基于关键词匹配)。

    Args:
        doc_content: OCR 文档内容。
        fields: 必填字段名,逗号分隔(如"项目名称,供应商,金额,日期")。
    """
    missing = []
    for field in fields.split(","):
        field = field.strip()
        if field and field not in doc_content:
            missing.append(field)
    if missing:
        return f'{{"pass": false, "detail": "缺失字段: {", ".join(missing)}"}}'
    return '{"pass": true, "detail": "所有必填字段均存在"}'
