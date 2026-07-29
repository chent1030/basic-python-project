"""工具系统 —— 全局工具注册表 + 自动发现 + @tool 装饰器。

两层工具:
- 全局共享工具:app/ai/tools/<name>.py,每个文件定义工具,import 即注册。
                工具多了拆成多个文件,不会膨胀(避免单文件越来越大)。
- agent 专属工具:app/ai/agents/<name>/tools.py,registry import 该 agent 目录时自动加载。
                agent config.yml 的 tools 字段引用全局工具名;专属工具自动全挂载。

工具用普通 Python 函数 + @tool 装饰器定义。各后端(deepagents/agentscope)在
build_agent 时把统一注册的函数转换成各自需要的工具格式(LangChain BaseTool /
agentscope Tool),转换逻辑在 backends/ 里,这里只存原始函数 + 元数据。

用法:
    # app/ai/tools/web_search.py 或 app/ai/agents/researcher/tools.py
    from app.ai.tools import tool

    @tool("web_search")
    async def web_search(query: str) -> str:
        '''搜索网络并返回结果。'''   # docstring = 工具描述(给 LLM 看)
        ...
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from app.core.config import settings
from app.core.logging_config import get_logger

log = get_logger("app.ai.tools")


@dataclass
class ToolDef:
    """工具定义:原始函数 + 元数据(名字/描述/参数 schema)。

    各后端从这里构造自己需要的工具对象。参数 schema 从函数签名 + type hints 提取。
    """

    name: str
    func: Callable[..., Any]
    description: str  # docstring(去首尾空白),给 LLM 看的工具说明
    params: dict[str, str] = field(default_factory=dict)  # 参数名 -> 类型名(str)
    is_async: bool = False
    namespace: str = ""  # 来源:"global" 或 agent 名(专属工具)


# 全局注册表:name -> ToolDef
_REGISTRY: dict[str, ToolDef] = {}


def tool(name: str):
    """装饰器:把函数注册成一个工具。

    Args:
        name: 工具名(全局唯一;同名后注册的会覆盖,专属工具可带 agent 名前缀避免冲突)。

    工具描述取 docstring;参数类型取 type hints。同步/异步函数都支持。
    """

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        desc = (inspect.getdoc(func) or "").strip()
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        params: dict[str, str] = {}
        sig = inspect.signature(func)
        for pname, _p in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            params[pname] = getattr(hints.get(pname, str), "__name__", str(hints.get(pname, "str")))
        is_async = inspect.iscoroutinefunction(func)
        _REGISTRY[name] = ToolDef(
            name=name,
            func=func,
            description=desc,
            params=params,
            is_async=is_async,
            namespace="global",
        )
        log.debug("注册工具 '%s' (%s, async=%s)", name, func.__qualname__, is_async)
        return func  # 返回原函数,不改其行为

    return deco


def _register_namespaced(namespace: str, func: Callable, name: str) -> None:
    """把一个函数按「专属工具」注册(带 namespace 标记,不改名)。"""
    tdef = _REGISTRY.get(name)
    if tdef is None:
        # 重新走一遍提取逻辑
        desc = (inspect.getdoc(func) or "").strip()
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        params = {}
        for pname, _p in inspect.signature(func).parameters.items():
            if pname in ("self", "cls"):
                continue
            params[pname] = getattr(hints.get(pname, str), "__name__", str(hints.get(pname, "str")))
        tdef = ToolDef(
            name=name, func=func, description=desc, params=params,
            is_async=inspect.iscoroutinefunction(func), namespace=namespace,
        )
        _REGISTRY[name] = tdef
    else:
        tdef.namespace = namespace


def get_tool(name: str) -> ToolDef | None:
    return _REGISTRY.get(name)


def all_tools() -> dict[str, ToolDef]:
    return dict(_REGISTRY)


def clear_tools() -> None:
    _REGISTRY.clear()


def resolve_tools(
    global_names: list[str], *, exclusive_agent: str | None = None
) -> list[ToolDef]:
    """解析一个 agent 要用的工具列表。

    Args:
        global_names:    config.yml 里 tools 字段声明的全局工具名。
        exclusive_agent: 若给定,把该 agent 命名空间下的专属工具也加进来
                         (namespace == exclusive_agent 的工具)。

    未知全局工具名只记 warning 不报错(避免一个坏工具阻塞 agent)。
    """
    result: list[ToolDef] = []
    seen: set[str] = set()
    for n in global_names:
        tdef = _REGISTRY.get(n)
        if tdef is None:
            log.warning("工具 '%s' 未注册(跳过)", n)
            continue
        if n not in seen:
            result.append(tdef)
            seen.add(n)
    if exclusive_agent:
        for tdef in _REGISTRY.values():
            if tdef.namespace == exclusive_agent and tdef.name not in seen:
                result.append(tdef)
                seen.add(tdef.name)
    return result


def discover_global_tools() -> None:
    """自动发现全局工具:import app.ai.tools 下所有子模块(触发 @tool 注册)。

    仿 scheduler._scan_tasks_package:扫描 tools_dir,逐个 import。
    只扫一次(由 gateway startup 调)。专属工具由 registry 在加载各 agent 时 import。
    """
    tools_pkg = settings.agents.tools_dir.replace("/", ".")  # app.ai.tools
    import importlib
    import pkgutil

    try:
        pkg = importlib.import_module(tools_pkg)
    except ImportError:
        log.info("全局工具包不存在,跳过扫描: %s", tools_pkg)
        return

    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return
    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg_path):
        full_name = f"{tools_pkg}.{mod_name}"
        try:
            importlib.import_module(full_name)
        except Exception:
            log.exception("导入全局工具模块失败: %s", full_name)


__all__ = [
    "ToolDef",
    "tool",
    "get_tool",
    "all_tools",
    "resolve_tools",
    "discover_global_tools",
    "clear_tools",
]
