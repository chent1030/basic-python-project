"""示例定时任务 —— 可参考这里的写法,或直接删除。

写法定义在 app/core/scheduler.py 的 @scheduled 装饰器。
支持:
    @scheduled(cron="0 0 * * *")    每天 0 点
    @scheduled(seconds=300)         每 5 分钟(固定间隔)
    @scheduled(minutes=10)          每 10 分钟
任务函数可以是 async def 或普通 def。
"""
from __future__ import annotations

import logging

from app.core.scheduler import scheduled

log = logging.getLogger("app.tasks.demo")


@scheduled(cron="0 0 * * *", misfire_grace_time=300)
async def daily_report() -> None:
    """每天凌晨生成日报(示例)。"""
    log.info("[定时任务] 执行 daily_report - 生成日报")
    # TODO: 查数据库、调 LLM、发邮件等真实业务逻辑


@scheduled(seconds=60)
async def heartbeat() -> None:
    """每分钟心跳上报(示例,固定间隔)。"""
    log.debug("[定时任务] heartbeat tick")


@scheduled(minutes=5)
def sync_external_data() -> None:
    """每 5 分钟同步外部数据(示例,同步函数自动丢线程池)。"""
    log.info("[定时任务] 执行 sync_external_data - 同步外部数据")
