"""聚合器(aggregator)—— pipeline 并行步骤的结果合并。

并行步骤的多个 agent 都跑完后,用 aggregator 把它们的输出合并成一个文本,
传给 pipeline 的下一步骤。

内置聚合器:
- merge:拼接所有输出(带 agent 名标注,默认)
- list:  返回 JSON 数组 [{agent, output}, ...]
- first: 取第一个(完成顺序)的输出

自定义聚合器:在 pipeline agent 目录下放 aggregator.py,用 @aggregator 装饰器定义:
    # app/ai/agents/<pipeline名>/aggregator.py
    from app.ai.aggregators import aggregator

    @aggregator("my_merge")
    def my_merge(outputs: list[tuple[str, str]]) -> str:
        # outputs: [(agent名, 输出文本), ...]
        return "\\n".join(f"- {n}: {o}" for n, o in outputs)

然后在 config.yml 的并行步骤里 aggregator: my_merge 引用。
registry 加载该 pipeline agent 时自动 import aggregator.py 注册。

所有聚合器签名统一:(list[tuple[str, str]]) -> str
  入参:[(agent名, 该 agent 的输出文本), ...](按 members 顺序)
  返回:合并后的文本(喂给 pipeline 下一步)
"""
from __future__ import annotations

import json
from collections.abc import Callable

from app.core.logging_config import get_logger

log = get_logger("app.ai.aggregators")

# 聚合函数类型:(agent名, 输出) 列表 -> 合并后的文本
AggregatorFunc = Callable[[list[tuple[str, str]]], str]

# 全局注册表:聚合器名 -> 函数
_REGISTRY: dict[str, AggregatorFunc] = {}


def aggregator(name: str):
    """装饰器:注册一个聚合器(pipeline 并行步骤结果合并用)。

    Args:
        name: 聚合器名(config.yml 里 aggregator 字段引用此名)。

    函数签名:(outputs: list[tuple[str, str]]) -> str
        outputs: [(agent名, 输出文本), ...],按 parallel members 声明顺序。
        返回:合并后的文本字符串。
    """

    def deco(func: AggregatorFunc) -> AggregatorFunc:
        _REGISTRY[name] = func
        log.debug("注册聚合器 '%s' (%s)", name, func.__qualname__)
        return func

    return deco


def get_aggregator(name: str) -> AggregatorFunc:
    """按名取聚合器。找不到返回默认 merge。"""
    if name not in _REGISTRY:
        log.warning("聚合器 '%s' 未注册,用默认 merge", name)
        return _REGISTRY["merge"]
    return _REGISTRY[name]


def all_aggregators() -> dict[str, AggregatorFunc]:
    return dict(_REGISTRY)


def clear_aggregators() -> None:
    """清掉自定义聚合器,保留内置(测试用)。"""
    builtin = {"merge", "list", "first"}
    for k in list(_REGISTRY):
        if k not in builtin:
            del _REGISTRY[k]


# --------------------------------------------------------------------------
# 内置聚合器
# --------------------------------------------------------------------------
@aggregator("merge")
def _merge(outputs: list[tuple[str, str]]) -> str:
    """拼接所有输出,带 agent 名标注(默认)。"""
    return "\n\n".join(f"## {n}\n{o}" for n, o in outputs)


@aggregator("list")
def _list(outputs: list[tuple[str, str]]) -> str:
    """返回 JSON 数组 [{agent, output}, ...]。"""
    return json.dumps([{"agent": n, "output": o} for n, o in outputs], ensure_ascii=False)


@aggregator("first")
def _first(outputs: list[tuple[str, str]]) -> str:
    """取第一个(声明顺序)的输出。"""
    return outputs[0][1] if outputs else ""


__all__ = [
    "AggregatorFunc",
    "aggregator",
    "get_aggregator",
    "all_aggregators",
    "clear_aggregators",
]
