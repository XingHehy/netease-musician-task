"""FastAPI 入口：挂载 API 路由、静态资源、WebSocket，启动浏览器 worker + 调度器。"""

from __future__ import annotations

import asyncio
import os
import shutil

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.browser.manager import worker
from app.config import APP_DIR, HOST, PORT
from app.db import init_db
from app.event_bus import bus
from app.logging_conf import logger
from app.web_auth import SESSION_COOKIE, ensure_admin_password, get_session

app = FastAPI(title="网易音乐人任务管理")

WEB_DIR = os.path.join(APP_DIR, "web")
STATIC_DIR = os.path.join(WEB_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def require_web_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path in {"/login", "/api/auth/login"}:
        return await call_next(request)

    session = get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "请先登录管理端"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    if session.must_change_password and path not in {"/change-password", "/api/auth/change-password", "/api/auth/logout"}:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "必须先修改默认管理密码"}, status_code=403)
        return RedirectResponse("/change-password", status_code=303)
    return await call_next(request)


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    ensure_admin_password()
    bus.bind_loop(asyncio.get_running_loop())
    worker.start()
    from app.scheduler import start as start_scheduler

    start_scheduler()
    logger.info("应用启动完成")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    index_path = os.path.join(WEB_DIR, "templates", "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/login", response_class=HTMLResponse)
@app.get("/change-password", response_class=HTMLResponse)
async def auth_page() -> str:
    auth_path = os.path.join(WEB_DIR, "templates", "auth.html")
    with open(auth_path, "r", encoding="utf-8") as f:
        return f.read()


# 路由注册
from app.api import accounts as accounts_api  # noqa: E402
from app.api import auth as auth_api  # noqa: E402
from app.api import debug as debug_api  # noqa: E402
from app.api import login as login_api  # noqa: E402
from app.api import settings as settings_api  # noqa: E402
from app.api import tasks as tasks_api  # noqa: E402
from app.api import ws as ws_api  # noqa: E402

app.include_router(accounts_api.router)
app.include_router(auth_api.router)
app.include_router(debug_api.router)
app.include_router(login_api.router)
app.include_router(tasks_api.router)
app.include_router(settings_api.router)
app.include_router(ws_api.router)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
