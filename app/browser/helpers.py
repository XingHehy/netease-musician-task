"""跨 frame 的元素操作辅助 + cookie 工具。登录与任务模块共用。"""

from __future__ import annotations

import time
from typing import Optional

from playwright.sync_api import Frame, Page


def scopes(page: Page | Frame):
    """遍历 main frame + 所有子 frame，处理弹窗在不同 frame 的情况。"""
    yield page
    frames = page.frames if isinstance(page, Page) else page.page.frames
    main = page.main_frame if isinstance(page, Page) else page.page.main_frame
    for fr in frames:
        if fr is main:
            continue
        yield fr


def first_with_selector(page: Page, selector: str) -> Page | Frame:
    for scope in scopes(page):
        try:
            if scope.locator(selector).count() > 0:
                return scope
        except Exception:
            continue
    return page


def click_first(page: Page | Frame, locator_or_text: str, *, exact_text: bool = False, timeout: int = 15000):
    """在 main frame + 所有 iframe 中找到第一个可点击目标并点击（持续重扫）。"""
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                if exact_text:
                    loc = scope.get_by_text(locator_or_text, exact=True)
                else:
                    loc = scope.locator(locator_or_text)
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=5000)
                loc.first.click()
                return scope
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法点击目标：{locator_or_text}")


def try_click_if_visible(page: Page | Frame, text: str, *, exact_text: bool = True, timeout_ms: int = 3000) -> bool:
    """有则点、无则跳过，不抛错。"""
    deadline = time.time() + max(0.5, timeout_ms / 1000)
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                loc = scope.get_by_text(text, exact=True) if exact_text else scope.locator(f"text={text}")
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=500)
                loc.first.click()
                return True
            except Exception:
                continue
        time.sleep(0.2)
    return False


def fill_first(page: Page | Frame, selector: str, value: str, *, timeout: int = 15000):
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                loc = scope.locator(selector)
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=500)
                loc.first.fill(value)
                return scope
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法填充：{selector}")


def check_first(page: Page | Frame, selector: str, *, timeout: int = 15000):
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                loc_all = scope.locator(selector)
                if loc_all.count() == 0:
                    continue
                loc = loc_all.first
                loc.wait_for(state="attached", timeout=500)
                loc.check(force=True)
                return scope
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法勾选：{selector}")


def cookies_to_str(cookies: list[dict]) -> str:
    pairs = []
    for c in cookies:
        name, value = c.get("name"), c.get("value")
        if name and value is not None:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def has_login_cookie(cookies: list[dict]) -> bool:
    """MUSIC_U 或 __csrf 任一存在即认为已登录。"""
    for c in cookies:
        if c.get("name") in ("MUSIC_U", "__csrf") and c.get("value"):
            return True
    return False
