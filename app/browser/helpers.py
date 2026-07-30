"""跨 frame 的元素操作辅助 + cookie 工具。登录与任务模块共用。"""

from __future__ import annotations

import time
from typing import Optional

from playwright.sync_api import Frame, Page


ACCOUNT_INFO_URL = "https://music.163.com/api/nuser/account/get"


def scopes(page: Page | Frame):
    """遍历 main frame + 所有子 frame，处理弹窗在不同 frame 的情况。

    注意：调用方在对每个 scope 使用等待型 API（wait_for / wait_for_function 等）
    之前，必须先用不带等待的 count() 判断目标元素是否存在于该 scope，否则每个
    不含目标的 frame 都会死等满一份 timeout。本文件下面的 helper 均遵循此约定。
    """
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
    """仅判断本地是否留有登录 cookie；不能证明服务端会话仍有效。"""
    for c in cookies:
        if c.get("name") in ("MUSIC_U", "__csrf") and c.get("value"):
            return True
    return False


def fetch_session_user(page: Page, home_url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """通过服务端账号接口验证会话，返回 (uid, nickname, error)。"""
    try:
        if "music.163.com" not in (page.url or ""):
            page.goto(home_url, wait_until="domcontentloaded")
        result = page.evaluate(
            f"""async () => {{
                const r = await fetch({ACCOUNT_INFO_URL!r}, {{
                    method: 'GET', credentials: 'include', cache: 'no-store'
                }});
                if (!r.ok) return {{__http_status: r.status}};
                return await r.json();
            }}"""
        )
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)

    if not isinstance(result, dict):
        return None, None, "账号信息接口返回非 JSON 对象"
    if result.get("__http_status"):
        return None, None, f"账号信息接口 HTTP {result['__http_status']}"

    profile = result.get("profile") or {}
    account = result.get("account") or {}
    uid = profile.get("userId") if isinstance(profile, dict) else None
    nickname = profile.get("nickname") if isinstance(profile, dict) else None
    if uid is None and isinstance(account, dict):
        uid = account.get("id")
    if uid is None:
        code = result.get("code")
        return None, None, f"账号信息接口未返回 uid（code={code!r}）"
    return str(uid), nickname, None
