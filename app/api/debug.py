"""通过受管理端登录保护的接口查看调试截图。"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app import repository as repo
from app.config import DEBUG_DIR

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/screenshots/{account_id}/{filename}")
def debug_screenshot(account_id: int, filename: str):
    account = repo.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", filename) or os.path.basename(filename) != filename:
        raise HTTPException(400, "无效的截图文件名")

    phone_dir = re.sub(r"[^\d+]+", "_", str(account["phone"]).strip()).strip("_") or "unknown"
    base_dir = os.path.realpath(os.path.join(DEBUG_DIR, phone_dir))
    path = os.path.realpath(os.path.join(base_dir, filename))
    if os.path.dirname(path) != base_dir or not os.path.isfile(path):
        raise HTTPException(404, "截图不存在")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )
