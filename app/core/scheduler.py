"""定时任务 —— APScheduler + 装饰器注册 + 自动扫描 app/tasks/。

用法(在 app/tasks/ 下任意 .py 文件里):
    from app.core.scheduler import scheduled

    @scheduled(cron="0 0 * * *")         # 每天 0 点(类似 Spring @Scheduled cron)
    async def daily_cleanup():
        ...

    @scheduled(seconds=300)              # 每 5 分钟(固定间隔)
    async def sync_data():
        ...

    @scheduled(minutes=10)               # 每 10 分钟(语义糖)
    def check_health():                  # 同步函数也支持,自动丢线程池
        ...

生命周期:
- startup:扫描 app/tasks/,把所有 @scheduled 注册的函数加进调度器并启动
- shutdown:优雅关闭,等正在执行的任务完成

任务管理 endpoint:
- GET /api/v1/tasks         列出所有已注册任务(下次执行时间等)
- POST /api/v1/tasks/{id}/run   手动触发一次某任务
- POST /api/v1/tasks/{id}/pause 暂停
- POST /api/v1/tasks/{id}/resume 恢复
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings

log = logging.getLogger("app.scheduler")


# --------------------------------------------------------------------------
# Registry:装饰器收集到的任务元数据
# --------------------------------------------------------------------------
@dataclass
class ScheduledTask:
    """一个被 @scheduled 装饰的任务的注册信息。"""

    func: Callable
    name: str                                   # 任务 id(函数全名 module.func)
    cron: str | None = None                     # cron 表达式
    seconds: int | None = None                  # 间隔秒
    minutes: int | None = None
    hours: int | None = None
    description: str = ""                       # 来自 docstring
    coalesce: bool = True                       # 错过的合并执行一次
    max_instances: int = 1                      # 同一任务最大并发实例
    misfire_grace_time: int | None = None       # 容忍超时秒数,None=用调度器默认
    extras: dict[str, Any] = field(default_factory=dict)


# 全局 registry:模块加载时收集,启动时消费
_REGISTRY: list[ScheduledTask] = []


def scheduled(
    *,
    cron: str | None = None,
    seconds: int | None = None,
    minutes: int | None = None,
    hours: int | None = None,
    coalesce: bool = True,
    max_instances: int = 1,
    misfire_grace_time: int | None = None,
) -> Callable:
    """把一个函数注册为定时任务(类似 Spring @Scheduled)。

    必须提供 cron / seconds / minutes / hours 中的至少一个。
    函数可以是 async def 也可以是普通 def(同步函数自动丢线程池执行)。

    Args:
        cron:                 cron 表达式,如 "0 0 * * *" "*/30 * * * *"
        seconds/minutes/hours:固定间隔(任选一个或组合)
        coalesce:             错过多次时只补执行一次(而不是全补)
        max_instances:        同一任务允许的并发实例数(默认 1,防重入)
        misfire_grace_time:   任务过期多久内仍可执行(秒);None=用调度器默认
    """
    has_cron = cron is not None
    has_interval = any(x is not None for x in (seconds, minutes, hours))
    if not has_cron and not has_interval:
        raise ValueError("@scheduled 必须指定 cron 或 seconds/minutes/hours 之一")

    def decorator(func: Callable) -> Callable:
        # 用 module.qualname 作为任务 id,确保唯一
        mod = getattr(func, "__module__", "unknown")
        qualname = getattr(func, "__qualname__", func.__name__)
        task_id = f"{mod}.{qualname}"

        task = ScheduledTask(
            func=func,
            name=task_id,
            cron=cron,
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            description=(func.__doc__ or "").strip().split("\n")[0] if func.__doc__ else "",
            coalesce=coalesce,
            max_instances=max_instances,
            misfire_grace_time=misfire_grace_time,
        )
        _REGISTRY.append(task)
        log.debug("注册定时任务: %s", task_id)
        return func  # 原样返回,不改变函数本身

    return decorator


# --------------------------------------------------------------------------
# Scheduler 单例:启动时扫描 app/tasks/ 并注册所有任务
# --------------------------------------------------------------------------
class SchedulerService:
    """APScheduler 包装,提供启动/停止/查询/手动执行。"""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._tasks: dict[str, ScheduledTask] = {}  # name -> meta

    # ---------- 生命周期 ---------------------------------------------
    async def startup(self) -> None:
        cfg = settings.scheduler

        # 1) 创建调度器
        scheduler = AsyncIOScheduler(
            timezone=cfg.timezone,
            job_defaults={
                "coalesce": cfg.coalesce,
                "max_instances": cfg.max_instances,
                "misfire_grace_time": cfg.misfire_grace_time,
            },
        )

        # 2) 扫描 app/tasks/ 触发所有 @scheduled 装饰器(收集进 _REGISTRY)
        _scan_tasks_package()

        # 3) 把 registry 里的任务转成 APScheduler job
        for task in _REGISTRY:
            self._tasks[task.name] = task
            trigger = _build_trigger(task)
            job_kwargs: dict[str, Any] = {
                "func": task.func,
                "trigger": trigger,
                "id": task.name,
                "name": task.func.__name__,
                "coalesce": task.coalesce,
                "max_instances": task.max_instances,
                "replace_existing": True,
            }
            if task.misfire_grace_time is not None:
                job_kwargs["misfire_grace_time"] = task.misfire_grace_time
            scheduler.add_job(**job_kwargs)

        self._scheduler = scheduler
        scheduler.start()
        log.info("调度器已启动,注册任务 %d 个", len(self._tasks))

    async def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            log.info("调度器已停止")

    def _ensure(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            raise RuntimeError("SchedulerService 尚未 startup,检查 lifespan")
        return self._scheduler

    # ---------- 查询 / 管理 -------------------------------------------
    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有已注册任务(含下次执行时间)。"""
        sched = self._ensure()
        out = []
        for job in sched.get_jobs():
            task = self._tasks.get(job.id)
            out.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "trigger": str(job.trigger),
                    "next_run_time": job.next_run_time.isoformat()
                    if job.next_run_time
                    else None,
                    "description": task.description if task else "",
                }
            )
        return out

    def run_job(self, job_id: str) -> None:
        """手动触发某任务执行一次(不影响原调度)。"""
        sched = self._ensure()
        sched.modify_job(job_id, next_run_time=datetime.now())

    def pause_job(self, job_id: str) -> None:
        sched = self._ensure()
        sched.pause_job(job_id)

    def resume_job(self, job_id: str) -> None:
        sched = self._ensure()
        sched.resume_job(job_id)


# --------------------------------------------------------------------------
# 触发器构造
# --------------------------------------------------------------------------
def _build_trigger(task: ScheduledTask) -> CronTrigger | IntervalTrigger:
    if task.cron:
        # 支持 "0 0 * * *" 这种空格分隔格式
        return CronTrigger.from_crontab(task.cron, timezone=settings.scheduler.timezone)
    return IntervalTrigger(
        seconds=task.seconds or 0,
        minutes=task.minutes or 0,
        hours=task.hours or 0,
        timezone=settings.scheduler.timezone,
    )


def _scan_tasks_package() -> None:
    """导入 app.tasks 下所有模块,触发 @scheduled 装饰器收集。

    扫描只做一次(即使多次调用也只扫第一次)。
    """
    global _SCANNED
    if _SCANNED:
        return
    _SCANNED = True

    try:
        tasks_pkg = importlib.import_module("app.tasks")
    except ImportError:
        log.info("app.tasks 包不存在,跳过任务扫描")
        return

    pkg_path = getattr(tasks_pkg, "__path__", None)
    if not pkg_path:
        return

    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(pkg_path):
        full_name = f"app.tasks.{mod_name}"
        try:
            importlib.import_module(full_name)
        except Exception:
            log.exception("导入任务模块失败: %s", full_name)


_SCANNED = False


# Singleton
scheduler_service = SchedulerService()


__all__ = ["scheduled", "scheduler_service", "SchedulerService", "ScheduledTask"]
