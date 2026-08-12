"""检查项注册表 —— 自动发现 checks/ 下的所有检查项。

仿 app.core.scheduler._scan_tasks_package 的自动发现模式:
扫描 app/harness/checks/*.py,每个文件定义模块级变量 CHECK = XxxCheck(),
import 后收集到注册表。

加检查项 = 加一个 .py 文件(含 CHECK = XxxCheck()),零配置、零改代码。
"""
from __future__ import annotations

import importlib
import pkgutil

from app.core.logging_config import get_logger
from app.harness.base import BaseCheck

log = get_logger("app.harness.registry")

_CHECKS: dict[str, BaseCheck] = {}
_SCANNED = False


def discover_checks() -> dict[str, BaseCheck]:
    """扫描 app/harness/checks/ 下所有模块,收集 CHECK 实例。只扫一次。"""
    global _SCANNED
    if _SCANNED:
        return _CHECKS
    _SCANNED = True

    try:
        pkg = importlib.import_module("app.harness.checks")
    except ImportError:
        log.info("checks 包不存在,无检查项")
        return _CHECKS

    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return _CHECKS

    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg_path):
        full_name = f"app.harness.checks.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
            check = getattr(mod, "CHECK", None)
            if isinstance(check, BaseCheck) and check.name:
                _CHECKS[check.name] = check
                log.info(
                    "发现检查项 '%s' (type=%s, sections=%s)",
                    check.name, check.check_type, check.sections,
                )
        except Exception:
            log.exception("加载检查项模块失败: %s", full_name)

    log.info("检查项就绪: %d 个 %s", len(_CHECKS), list(_CHECKS) or "(无)")
    return _CHECKS


def get_check(name: str) -> BaseCheck | None:
    return _CHECKS.get(name)


def all_checks() -> dict[str, BaseCheck]:
    return dict(_CHECKS)


def clear() -> None:
    """清空(测试用)。"""
    global _SCANNED
    _CHECKS.clear()
    _SCANNED = False


__all__ = ["discover_checks", "get_check", "all_checks", "clear"]
