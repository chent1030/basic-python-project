"""product_pipeline 的自定义聚合器。

演示工程师如何自定义「并行步骤结果合并」逻辑:
- 内置 merge 只是简单拼接;这里按「竞品/痛点/趋势」分节整理,更像一份分析摘要。
- 聚合器签名统一:(outputs: list[tuple[str, str]]) -> str
    outputs: [(agent名, 该 agent 输出文本), ...],按 config 里 parallel 顺序。
    返回:合并后的文本(喂给 pipeline 下一步,这里是 report_writer)。

注册:用 @aggregator("名字") 装饰,config.yml 里 aggregator: 名字 引用。
registry 加载本 pipeline agent 时自动 import 本文件触发注册。
"""
from __future__ import annotations

from app.ai.aggregators import aggregator

# agent 名 -> 中文小节标题的映射(让合并结果更可读)
SECTION_TITLES = {
    "competitor_analyst": "竞品分析",
    "painpoint_finder": "用户痛点",
    "trend_watcher": "市场趋势",
}


@aggregator("structured_summary")
def structured_summary(outputs: list[tuple[str, str]]) -> str:
    """把三路并行分析结果整理成分节摘要。

    相比内置 merge(简单拼接),这里:
    1. 用中文小节标题(竞品分析/用户痛点/市场趋势)而非 agent 名
    2. 加一段总起语,让下游 report_writer 更清楚这是多路汇总
    3. 未知 agent 兜底用其名做标题
    """
    lines = ["以下是三个维度的并行分析结果,请据此撰写报告:"]
    for agent_name, text in outputs:
        title = SECTION_TITLES.get(agent_name, agent_name)
        lines.append(f"\n### {title}\n{text}")
    return "\n".join(lines)
