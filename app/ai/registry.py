"""Agent 注册表 —— 扫描 agents 目录,自动发现所有 agent。

仿 app.core.scheduler._scan_tasks_package 的自动发现模式:
扫描 settings.agents.agents_dir 下每个含 config.yml 的子目录 → 加载成 AgentConfig。

去中心化:加/删 agent = 加/删一个目录,无需改任何注册代码。
每个 agent 目录可选含 tools.py(专属工具,import 即注册)和 agent.py(复杂编排)。

成员引用解析:多拓扑(subagent/members/routes)引用的是其它 agent 目录名,
运行时由 runners 通过 registry.get(name) 按需解析,不在此处递归加载(避免循环)。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from app.ai.config import AgentConfig
from app.core.config import settings
from app.core.logging_config import get_logger

log = get_logger("app.ai.registry")


def _resolve_agents_dir() -> Path:
    """agents_dir 是相对项目根的路径,解析成绝对路径。"""
    raw = settings.agents.agents_dir
    p = Path(raw)
    if not p.is_absolute():
        # app/core/config.py -> 项目根是 parents[2];app/ai/registry.py -> parents[2]
        root = Path(__file__).resolve().parents[2]
        p = root / p
    return p


class AgentRegistry:
    """管理所有已加载的 agent 配置,按 name 取。

    生命周期:startup() 扫描加载;之后只读。修改目录后重启生效(与 scheduler 一致)。
    """

    def __init__(self) -> None:
        self._configs: dict[str, AgentConfig] = {}
        self._loaded: bool = False

    # ---------- lifecycle ---------------------------------------------
    def load(self) -> None:
        """扫描 agents_dir,加载所有含 config.yml 的子目录。只扫一次。"""
        if self._loaded:
            return
        self._loaded = True

        agents_dir = _resolve_agents_dir()
        if not agents_dir.exists():
            log.info("agents 目录不存在,未加载任何 agent: %s", agents_dir)
            return

        count = 0
        for child in sorted(agents_dir.iterdir()):
            if not child.is_dir():
                continue
            cfg_path = child / "config.yml"
            if not cfg_path.exists():
                # 也支持 config.yaml 扩展名
                cfg_path = child / "config.yaml"
                if not cfg_path.exists():
                    continue
            name = child.name
            try:
                cfg = AgentConfig.from_yaml_file(cfg_path)
                self._configs[name] = cfg
                count += 1
                log.info(
                    "已加载 agent '%s' (topology=%s, backend=%s, mode=%s)",
                    name, cfg.topology, cfg.backend, cfg.mode,
                )
            except Exception:
                log.exception("加载 agent '%s' 失败(跳过)", name)

        # 加载完配置后,import 各 agent 目录的专属 tools.py(注册专属工具)
        self._import_agent_modules(agents_dir)

        log.info("agent registry 就绪: %d 个 agent %s", count, list(self._configs) or "(无)")

    def _import_agent_modules(self, agents_dir: Path) -> None:
        """import 每个 agent 目录的 tools.py(若有),触发 @tool 装饰器注册专属工具。

        走 Python 包路径 app.ai.agents.<name>.tools,不走文件系统 import,
        这样工具函数的 __module__ 正确、可被各后端库识别。

        若 agents_dir 不在 Python 包路径下(如临时目录),模块名无效,
        此时直接跳过专属工具 import(不影响 agent 本身加载)。
        """
        pkg_base = settings.agents.agents_dir.replace("/", ".")
        # 绝对路径或含非法字符时不是合法包名,跳过专属工具 import
        is_abs = Path(settings.agents.agents_dir).is_absolute()
        sane = pkg_base.replace(".", "").replace("_", "").isalnum()
        if is_abs or not sane:
            return
        for name in list(self._configs):
            for sub in ("tools", "agent", "aggregator"):
                mod_name = f"{pkg_base}.{name}.{sub}"
                try:
                    importlib.import_module(mod_name)
                except ModuleNotFoundError:
                    # 该 agent 没有这个子模块,正常,跳过
                    pass
                except Exception:
                    log.exception("导入 agent '%s' 的 %s 模块失败", name, sub)

    def clear(self) -> None:
        self._configs.clear()
        self._loaded = False

    # ---------- query -------------------------------------------------
    def get(self, name: str) -> AgentConfig:
        if name not in self._configs:
            available = list(self._configs) or "(无)"
            raise KeyError(
                f"agent '{name}' 未注册。可用: {available}。"
                f"在 {settings.agents.agents_dir}/ 下建 <name>/config.yml 即可。"
            )
        return self._configs[name]

    def has(self, name: str) -> bool:
        return name in self._configs

    def names(self) -> list[str]:
        return list(self._configs)

    def all(self) -> dict[str, AgentConfig]:
        return dict(self._configs)

    def info(self, name: str) -> dict[str, Any]:
        """agent 元信息摘要(给 API 用)。"""
        cfg = self.get(name)
        return {
            "name": name,
            "topology": cfg.topology,
            "backend": cfg.backend,
            "mode": cfg.mode,
            "provider": cfg.provider,
            "tools": cfg.tools,
            "subagents": cfg.subagents,
            "members": cfg.members,
            "routes": cfg.routes,
            "middleware": [m.name for m in cfg.middleware],
        }


# 单例 —— gateway startup 时 load()。
registry = AgentRegistry()


__all__ = ["AgentRegistry", "registry"]
