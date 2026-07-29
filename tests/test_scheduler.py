"""Tests for the scheduler module — @scheduled decorator + registry + service."""
from __future__ import annotations

import asyncio

import pytest

from app.core.scheduler import (
    _REGISTRY,
    SchedulerService,
    scheduled,
)


# ----------------------------------------------------------- decorator
def test_scheduled_cron_registers_task():
    """@scheduled(cron=...) 应把函数加入 registry。"""

    @scheduled(cron="0 0 * * *")
    async def my_daily_task():
        """daily"""

    assert any(t.func is my_daily_task for t in _REGISTRY)
    task = next(t for t in _REGISTRY if t.func is my_daily_task)
    assert task.cron == "0 0 * * *"
    assert task.seconds is None
    assert "my_daily_task" in task.name


def test_scheduled_interval_registers_task():
    @scheduled(seconds=120)
    def interval_task():
        pass

    task = next(t for t in _REGISTRY if t.func is interval_task)
    assert task.seconds == 120
    assert task.cron is None


def test_scheduled_minutes_hours():
    @scheduled(minutes=5, hours=2)
    def combo():
        pass

    task = next(t for t in _REGISTRY if t.func is combo)
    assert task.minutes == 5
    assert task.hours == 2


def test_scheduled_requires_trigger():
    """没有 cron 也没有 interval 应该报错。"""
    with pytest.raises(ValueError, match="必须指定"):

        @scheduled()
        def bad():
            pass


def test_scheduled_returns_func_unchanged():
    """装饰器不应改变原函数。"""

    @scheduled(cron="* * * * *")
    async def original():
        return 42

    # 函数仍可正常调用
    assert asyncio.run(original()) == 42


def test_scheduled_extracts_description_from_docstring():
    @scheduled(cron="* * * * *")
    async def documented_task():
        """这是任务说明。"""
        pass

    task = next(t for t in _REGISTRY if t.func is documented_task)
    assert task.description == "这是任务说明。"


def test_scheduled_custom_options():
    @scheduled(cron="* * * * *", coalesce=False, max_instances=3, misfire_grace_time=120)
    async def custom():
        pass

    task = next(t for t in _REGISTRY if t.func is custom)
    assert task.coalesce is False
    assert task.max_instances == 3
    assert task.misfire_grace_time == 120


# ----------------------------------------------------------- service lifecycle
@pytest.mark.asyncio
async def test_scheduler_service_startup_shutdown():
    """startup 应启动 AsyncIOScheduler,shutdown 应停止。"""
    svc = SchedulerService()
    await svc.startup()
    assert svc._scheduler is not None
    assert svc._scheduler.running

    await svc.shutdown()
    assert svc._scheduler is None


@pytest.mark.asyncio
async def test_scheduler_service_lists_jobs():
    """启动后应能列出已注册的任务(demo_tasks 里的 3 个)。"""
    svc = SchedulerService()
    await svc.startup()
    try:
        jobs = svc.list_jobs()
        # demo_tasks.py 里注册了 3 个任务
        assert len(jobs) >= 3
        # 每个任务有 id / trigger / next_run_time 字段
        for j in jobs:
            assert "id" in j
            assert "trigger" in j
    finally:
        await svc.shutdown()


@pytest.mark.asyncio
async def test_scheduler_service_run_pause_resume():
    """run_job / pause_job / resume_job 不应报错。"""
    svc = SchedulerService()
    await svc.startup()
    try:
        jobs = svc.list_jobs()
        assert len(jobs) > 0
        job_id = jobs[0]["id"]

        # 不应抛异常
        svc.pause_job(job_id)
        svc.resume_job(job_id)
        svc.run_job(job_id)  # 触发立即执行
    finally:
        await svc.shutdown()


@pytest.mark.asyncio
async def test_scheduler_service_before_startup_raises():
    svc = SchedulerService()
    with pytest.raises(RuntimeError, match="尚未 startup"):
        svc.list_jobs()
