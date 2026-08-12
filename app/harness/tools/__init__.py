"""工具系统 —— @tool 装饰器 + 注册表 + resolve。

工具用普通 Python 函数 + @tool 装饰器定义。各后端自动转换格式。
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints


@dataclass
class ToolDef:
    name: str
    func: Callable[..., Any]
    description: str
    params: dict[str, str] = field(default_factory=dict)
    is_async: bool = False


_REGISTRY: dict[str, ToolDef] = {}


def tool(name: str):
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        desc = (inspect.getdoc(func) or "").strip()
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        params: dict[str, str] = {}
        for pname in inspect.signature(func).parameters:
            if pname in ("self", "cls"):
                continue
            params[pname] = getattr(
                hints.get(pname, str), "__name__", str(hints.get(pname, "str"))
            )
        _REGISTRY[name] = ToolDef(
            name=name, func=func, description=desc,
            params=params, is_async=inspect.iscoroutinefunction(func),
        )
        return func

    return deco


def resolve_tools(names: list[str]) -> list[ToolDef]:
    result: list[ToolDef] = []
    for n in names:
        t = _REGISTRY.get(n)
        if t:
            result.append(t)
    return result


def get_tool(name: str) -> ToolDef | None:
    return _REGISTRY.get(name)


def all_tools() -> dict[str, ToolDef]:
    return dict(_REGISTRY)


def discover_tools() -> None:
    """自动发现 tools/ 下的工具模块（import 触发 @tool 注册）。

    仿 scheduler._scan_tasks_package：扫描本包下所有子模块。
    """
    import importlib
    import pkgutil

    pkg = importlib.import_module("app.harness.tools")
    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return
    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg_path):
        if mod_name == "__init__":
            continue
        try:
            importlib.import_module(f"app.harness.tools.{mod_name}")
        except Exception:
            import logging

            logging.getLogger("app.harness.tools").exception(
                "导入工具模块失败: %s", mod_name
            )


__all__ = [
    "tool", "ToolDef", "resolve_tools", "get_tool", "all_tools",
    "discover_tools",
]
