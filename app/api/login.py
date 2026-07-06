"""登录触发：后台启动浏览器登录，进度经 WebSocket 推送。"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException

from app import repository as repo
from app.logging_conf import logger

router = APIRouter(prefix="/api/login", tags=["login"])


@router.post("/{account_id}")
def start_login(account_id: int) -> dict:
    acc = repo.get_account(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")

    # 在后台线程调用 runner.run_login（其内部再投递到 browser worker 串行执行），
    # 立即返回，前端通过 WebSocket 看实时进度。
    def _bg() -> None:
        try:
            from app.runner import run_login

            run_login(account_id)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"后台登录异常：{e}")

    threading.Thread(target=_bg, name=f"login-{account_id}", daemon=True).start()
    return {"ok": True, "message": "登录已在后台启动，请查看实时日志"}
