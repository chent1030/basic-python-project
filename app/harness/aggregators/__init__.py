"""聚合器 —— pipeline/parallel 的结果合并。"""
from __future__ import annotations

import json
from collections.abc import Callable

AggregatorFunc = Callable[[list[tuple[str, str]]], str]
_REGISTRY: dict[str, AggregatorFunc] = {}

def aggregator(name: str):
    def deco(func):
        _REGISTRY[name] = func
        return func
    return deco

def get_aggregator(name: str) -> AggregatorFunc:
    if name not in _REGISTRY:
        return _REGISTRY["merge"]
    return _REGISTRY[name]

@aggregator("merge")
def _merge(outputs):
    return "\n\n".join(f"## {n}\n{o}" for n, o in outputs)

@aggregator("list")
def _list(outputs):
    return json.dumps([{"agent": n, "output": o} for n, o in outputs], ensure_ascii=False)

@aggregator("first")
def _first(outputs):
    return outputs[0][1] if outputs else ""

__all__ = ["aggregator", "get_aggregator", "AggregatorFunc"]
