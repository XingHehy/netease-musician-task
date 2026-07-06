"""
浏览器 worker：每个浏览器任务跑在独立线程。

并发策略：靠「抢占」保证同一时刻只有一个浏览器在跑——
提交新任务前先强制结束已有浏览器进程（registry.preempt_existing），
这样即使旧任务卡在扫码等待/挂起的 Playwright 调用上，其浏览器进程被杀后
会立即抛错退出，不会阻塞新任务。

用法：
    submit(fn, *args, **kwargs) -> Future
    fn 在独立线程内执行；Playwright 生命周期由 run_with_context 提供。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable

from app.config import BROWSER_TIMEOUT_MS, HEADLESS, USER_AGENT
from app.browser.selectors import STEALTH_SCRIPT
from app.logging_conf import logger


class BrowserWorker:
    def __init__(self) -> None:
        self._started = False

    def start(self) -> None:
        # 无常驻线程，保留接口兼容 main.py 的启动调用
        self._started = True
        logger.info("浏览器 worker 已就绪（抢占式单浏览器）")

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        # 抢占：强制结束已有浏览器，保证同一时刻只有一个浏览器在跑。
        try:
            from app.browser import registry

            registry.preempt_existing()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"抢占旧浏览器失败：{e}")

        fut: Future = Future()

        def _run() -> None:
            try:
                fut.set_result(fn(*args, **kwargs))
            except Exception as e:  # noqa: BLE001
                logger.exception(f"浏览器任务执行异常：{e}")
                fut.set_exception(e)

        threading.Thread(target=_run, name="browser-task", daemon=True).start()
        return fut


# 全局单例
worker = BrowserWorker()


@contextmanager
def run_with_context(profile_dir: str, *, headless: bool | None = None, account_id: int | None = None, label: str = ""):
    """
    在 worker 线程内打开一个持久化 Playwright context，yield (context, page)。
    退出时自动关闭。必须在 worker 线程内调用（Playwright 同步 API 线程亲和）。
    打开后登记浏览器进程，供抢占逻辑跨线程强制结束。
    """
    from playwright.sync_api import sync_playwright
    from app.browser import registry

    os.makedirs(profile_dir, exist_ok=True)

    # headless 优先取运行期 settings（网页可改），未指定时回退到启动配置
    if headless is None:
        try:
            from app.repository import get_setting_bool

            use_headless = get_setting_bool("headless", HEADLESS)
        except Exception:
            use_headless = HEADLESS
    else:
        use_headless = headless

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=use_headless,
            viewport={"width": 1280, "height": 800},
            user_agent=USER_AGENT,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()
        page.set_default_timeout(BROWSER_TIMEOUT_MS)

        # 登记浏览器进程（driver 进程树含 chromium），供抢占强制结束
        pid = None
        try:
            pid = context._impl_obj._connection._transport._proc.pid
            registry.register(pid, account_id, label)
        except Exception:
            pass

        try:
            yield context, page
        finally:
            if pid is not None:
                registry.unregister(pid)
            try:
                context.close()
            except Exception:
                pass
