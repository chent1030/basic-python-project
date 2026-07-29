"""定时任务触发 agent 示例。

trigger 的来源不止 API:定时任务(scheduler)也能触发 agent。
任务内调 agent_gateway.trigger(source="scheduler"),运行记录里会标记来源。

仿 app/tasks/demo_tasks.py 的风格:用 @scheduled 装饰器,scheduler 自动发现。
"""
from __future__ import annotations

from app.ai.gateway import agent_gateway
from app.core.logging_config import get_logger
from app.core.scheduler import scheduled

log = get_logger("app.tasks.agent")


@scheduled(cron="0 9 * * *", misfire_grace_time=300)
async def daily_research() -> None:
    """每天 9 点触发 research_team agent 跑一轮研究(示例)。

    若该 agent 不存在(示例未启用),只记日志不报错,避免拖垮 scheduler。
    """
    from app.ai.registry import registry

    if not registry.has("researcher"):
        log.info("定时任务跳过:'researcher' agent 未注册(示例 agent)")
        return
    try:
        result = await agent_gateway.trigger(
            "researcher", "今日 AI 领域值得关注的技术动态", source="scheduler"
        )
        log.info("定时研究完成: %s", result.output[:200])
    except Exception:
        log.exception("定时研究任务失败")
