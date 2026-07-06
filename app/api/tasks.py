"""手动触发任务 + 查看日志。"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo
from app.logging_conf import logger

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_VALID_TASKS = {"checkin", "publish", "vip"}


class RunSelection(BaseModel):
    tasks: list[str]


@router.post("/{account_id}/run")
def run_selected(account_id: int, body: RunSelection) -> dict:
    if not repo.get_account(account_id):
        raise HTTPException(404, "账号不存在")
    tasks = [t for t in body.tasks if t in _VALID_TASKS]
    if not tasks:
        raise HTTPException(400, "请至少选择一项任务")

    def _bg() -> None:
        try:
            from app import runner

            runner.run_selected(account_id, tasks)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"后台任务异常：{e}")

    threading.Thread(target=_bg, name=f"run-{account_id}", daemon=True).start()
    return {"ok": True, "message": f"已在后台执行：{', '.join(tasks)}"}


@router.get("/logs")
def logs(account_id: int | None = None, limit: int = 100) -> list[dict]:
    return repo.list_logs(account_id, limit)


@router.get("/active")
def active() -> dict:
    """当前正在运行浏览器的账号信息（用于前端把「执行」按钮切成「查看」）。"""
    from app.browser import registry

    return {"active": registry.active_info()}


@router.get("/{account_id}/live")
def live_logs(account_id: int) -> dict:
    """拉取该账号的累积实时日志 + 最新二维码（供「查看」弹窗回看，不清空）。"""
    from app.event_bus import bus

    return {"logs": bus.get_buffer(account_id), "qr": bus.get_qr(account_id)}


@router.post("/{account_id}/stop")
def stop(account_id: int) -> dict:
    """强制停止该账号正在运行的浏览器任务。"""
    from app.browser import registry

    stopped = registry.force_stop(account_id)
    return {"ok": stopped, "message": "已强制停止" if stopped else "该账号当前没有正在运行的任务"}
