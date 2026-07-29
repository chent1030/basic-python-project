"""定时任务管理 endpoint。

只暴露只读 + 基本控制,任务本身的定义在 app/tasks/ 下用 @scheduled 装饰器。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.scheduler import scheduler_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks() -> dict[str, list[dict]]:
    """列出所有已注册的定时任务及下次执行时间。"""
    return {"tasks": scheduler_service.list_jobs()}


@router.post("/{job_id}/run")
async def run_task(job_id: str) -> dict[str, str]:
    """手动触发某任务立即执行一次(不影响原调度)。"""
    try:
        scheduler_service.run_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}") from e
    return {"status": "triggered", "job_id": job_id}


@router.post("/{job_id}/pause")
async def pause_task(job_id: str) -> dict[str, str]:
    """暂停某任务。"""
    try:
        scheduler_service.pause_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}") from e
    return {"status": "paused", "job_id": job_id}


@router.post("/{job_id}/resume")
async def resume_task(job_id: str) -> dict[str, str]:
    """恢复某任务。"""
    try:
        scheduler_service.resume_job(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}") from e
    return {"status": "resumed", "job_id": job_id}
