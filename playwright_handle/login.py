"""
使用 Playwright 打开网易云音乐 **手机号密码登录页**，自动完成你描述的所有点击和输入，
并把登录后的 Cookie 保存到 Redis（供 core.py/main.py 复用）。

使用前先在文件最上面改成你自己的手机号、密码、可选 uid。
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

import ddddocr
from playwright.sync_api import sync_playwright, Page, Frame

from core import NeteaseClient  # 仅用于本模块内部根据 Cookie 识别 uid

logger = logging.getLogger("netease_music")


# ======== 按需修改这里（作为脚本单独运行时使用） ========
LOGIN_URL = "https://music.163.com/#/login?targetUrl=https%3A%2F%2Fmusic.163.com%2Fst%2Fmusician"

# 作为脚本直接运行时的默认账号（集成到 main.py 时会传参覆盖）
PHONE = "17600000000"
PASSWORD = "your-password"

# 如果你知道自己的 uid，可以直接填；否则留 None，由后续逻辑识别
FIXED_UID: Optional[int] = None

# Playwright 持久化用户目录（可复用登录态）
PROFILE_DIR = ".playwright_profile_netease"


# ======== 工具函数 ========


def cookies_to_cookie_str(cookies: list[dict]) -> str:
    # 只拼接 name/value；domain/path/expiry 不需要给 requests 用
    pairs = []
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def try_get_uid_from_cookie(cookie_str: str) -> Optional[int]:
    """
    尝试用 Cookie 换取当前登录用户的 uid。
    """
    client = NeteaseClient(cookie_str=cookie_str)

    candidates = [
        ("GET", "/api/nuser/account/get", False, None),
        ("GET", "/api/w/nuser/account/get", False, None),
        ("GET", "/api/v1/user/info", False, None),
        ("POST", "/weapi/w/nuser/account/get", True, {}),
    ]

    for method, path, encrypt, data in candidates:
        try:
            res = client.request(method, path, data=data, encrypt=encrypt)
        except Exception as e:
            logger.warning(f"尝试获取 uid 失败：{method} {path} - {e}")
            continue

        if not isinstance(res, dict):
            continue

        uid = None
        account = res.get("account") or {}
        profile = res.get("profile") or {}
        if isinstance(account, dict):
            uid = account.get("id") or uid
        if isinstance(profile, dict):
            uid = profile.get("userId") or uid

        try:
            if uid is not None:
                return int(uid)
        except Exception:
            pass

    return None


def _scopes(page: Page | Frame):
    """
    返回可操作的 scope：优先 main frame，再遍历所有子 frame。
    这样弹窗在主文档/不同 frame 时都能继续流程。
    """
    yield page
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        yield fr


def _click_first(page: Page | Frame, locator_or_text: str, *, exact_text: bool = False, timeout: int = 15000):
    """
    在 main frame + 所有 iframe 中，找到第一个可点击的目标并点击。
    - locator_or_text: 支持 "text=xxx" / css / xpath 等；若 exact_text=True 则按纯文本匹配
    """
    # 关键点：登录弹窗/内部 frame 可能是“点击后才动态创建”的
    # 因此需要在 timeout 内不断重扫所有 frame，直到找到目标元素
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in _scopes(page):
            try:
                if exact_text:
                    loc = scope.get_by_text(locator_or_text, exact=True)
                else:
                    loc = scope.locator(locator_or_text)
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=500)
                loc.first.click()
                return scope
            except Exception as e:
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法点击目标：{locator_or_text}")


def _fill_first(page: Page | Frame, selector: str, value: str, *, timeout: int = 15000):
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in _scopes(page):
            try:
                loc_all = scope.locator(selector)
                if loc_all.count() == 0:
                    continue
                loc = loc_all.first
                loc.wait_for(state="visible", timeout=500)
                loc.fill(value)
                return scope
            except Exception as e:
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法输入：{selector}")


def _check_first(page: Page | Frame, selector: str, *, timeout: int = 15000):
    deadline = time.time() + max(1, timeout / 1000)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for scope in _scopes(page):
            try:
                loc_all = scope.locator(selector)
                if loc_all.count() == 0:
                    continue
                loc = loc_all.first
                loc.wait_for(state="attached", timeout=500)
                loc.check(force=True)
                return scope
            except Exception as e:
                last_err = e
                continue
        time.sleep(0.1)
    raise last_err or RuntimeError(f"无法勾选：{selector}")


def solve_slider_captcha(page: Page | Frame, max_retry: int = 3):
    """
    使用 ddddocr 识别网易云「滑块/拼图」验证码，并模拟拖动滑块。
    只做基础逻辑，失败会尝试刷新重试几次。
    """
    ocr = ddddocr.DdddOcr(det=False, ocr=False)

    for attempt in range(1, max_retry + 1):
        logger.info(f"尝试第 {attempt} 次滑块验证...")

        bg_bytes = None
        slider_bytes = None
        scope_used: Optional[Page | Frame] = None

        # 1. 在所有 frame 里找到背景图和滑块图
        for scope in _scopes(page):
            try:
                bg = scope.locator(".yidun_bg-img").first
                slider = scope.locator(".yidun_jigsaw").first
                if bg.count() == 0 or slider.count() == 0:
                    continue
                bg_bytes = bg.screenshot(type="png")
                slider_bytes = slider.screenshot(type="png")
                scope_used = scope
                break
            except Exception:
                continue

        if not bg_bytes or not slider_bytes or scope_used is None:
            logger.warning("未找到滑块验证码图片，可能当前没有触发滑块。")
            return

        # 2. 用 ddddocr 计算位移
        try:
            match_res = ocr.slide_match(target=bg_bytes, slider=slider_bytes)
            # 返回格式一般为 {"target": [x, y], "slider": [w, h]}
            target_x = match_res["target"][0]
        except Exception as e:
            logger.warning(f"ddddocr 识别滑块失败: {e}")
            return

        logger.info(f"滑块目标位移（粗略）: {target_x}")

        # 3. 找到滑块控件并拖动
        try:
            slider_handle = scope_used.locator(".yidun_slider__icon, .yidun_slider").first
            box = slider_handle.bounding_box()
            if not box:
                logger.warning("无法获取滑块控件的 bounding_box")
                return

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2

            # Playwright 鼠标拖动
            scope_used.page.mouse.move(start_x, start_y)
            scope_used.page.mouse.down()
            # 留一点冗余，避免不够
            end_x = start_x + target_x * 1.05
            steps = 30
            for i in range(steps):
                x = start_x + (end_x - start_x) * (i + 1) / steps
                scope_used.page.mouse.move(x, start_y, steps=1)
                time.sleep(0.02)
            scope_used.page.mouse.up()

            # 拖动结束后稍等，看是否还在
            time.sleep(2)
            # 如果控件还存在且提示失败，可以点刷新再重试一次
            if attempt < max_retry:
                try:
                    refresh_btn = scope_used.locator(".yidun_refresh").first
                    if refresh_btn.count() > 0:
                        refresh_btn.click()
                        time.sleep(1)
                        continue
                except Exception:
                    pass
            break
        except Exception as e:
            logger.warning(f"模拟拖动滑块失败: {e}")
            if attempt >= max_retry:
                return
            time.sleep(1)


def do_login_with_phone(page: Page | Frame, phone: str, password: str):
    """
    按你给的 DOM/文字说明，依次点击：
    1. 选择其他登录模式
    2. 勾选协议
    3. 手机号登录/注册
    4. 密码登录
    5. 输入手机号、密码
    6. 点击登录
    """
    # 1. 点击「选择其他登录模式」
    _click_first(page, "选择其他登录模式", exact_text=True)
    logger.info("已点击「选择其他登录模式」")

    # 2. 勾选协议复选框
    _check_first(page, "#j-official-terms")
    logger.info("已勾选协议复选框")

    # 3. 点击「手机号登录/注册」
    _click_first(page, "a:has(div:has-text('手机号登录/注册'))")
    logger.info("已点击「手机号登录/注册」")

    # 4. 等弹窗出来，点击「密码登录」
    # 注意：这一步经常出现在主文档的弹窗里，所以要重新在所有 scope 中找
    _click_first(page, "密码登录", exact_text=True, timeout=20000)
    logger.info("已点击「密码登录」")

    # 5. 输入手机号
    _fill_first(page, "input[placeholder='请输入手机号']", phone)
    logger.info("已输入手机号")

    # 6. 输入密码
    _fill_first(page, "input[placeholder='请输入密码']", password)
    logger.info("已输入密码")

    # 7. 点击「登录」
    _click_first(page, "a:has(div:has-text('登录'))")
    logger.info("已点击「登录」")


def browser_login(phone: str, password: str, profile_dir: str = PROFILE_DIR) -> str:
    """
    供核心逻辑调用的通用浏览器登录函数：
    - 使用 Playwright 完成手机号+密码登录（含滑块）
    - 返回 cookie_str，后续由 core.AuthManager 负责写入 Redis 等
    """
    if not phone or not password:
        raise ValueError("phone/password 不能为空")

    os.makedirs("log", exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        logger.info(f"使用 Playwright 打开登录页，账号：{phone}")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        logger.info("开始执行自动登录流程（main frame + 所有 iframe 自动探测）...")
        do_login_with_phone(page, phone, password)

        try:
            solve_slider_captcha(page)
        except Exception as e:
            logger.warning(f"滑块验证码处理过程出错：{e}")

        deadline = time.time() + 60
        cookie_str = ""
        while time.time() < deadline:
            cookies = context.cookies("https://music.163.com")
            cookie_str = cookies_to_cookie_str(cookies)

            has_music_u = any(c.get("name") == "MUSIC_U" and c.get("value") for c in cookies)
            has_csrf = any(c.get("name") == "__csrf" and c.get("value") for c in cookies)
            if has_music_u or has_csrf:
                break
            time.sleep(1)

        context.close()

        if not cookie_str:
            raise RuntimeError("浏览器登录未获取到任何 Cookie，请检查是否登录成功。")

        return cookie_str


def main():
    """
    作为独立脚本运行时：
    - 使用上面的 PHONE / PASSWORD 登录
    - 自动识别 uid
    - 调用 core.AuthManager 写入 Redis
    """
    from core import AuthManager, NeteaseClient  # 延迟导入避免循环

    cookie_str = browser_login(PHONE, PASSWORD, PROFILE_DIR)

    uid = FIXED_UID or try_get_uid_from_cookie(cookie_str)
    if not uid:
        logger.warning("未能自动识别 uid，如需写入 Redis，请在文件顶部设置 FIXED_UID = 你的 uid。")
    else:
        logger.info(f"识别到 uid={uid}")

    if uid:
        auth = AuthManager()
        user_data = {}
        try:
            client = NeteaseClient(cookie_str=cookie_str, uid=uid)
            user_data = client.request("GET", f"/api/v1/user/detail/{uid}", encrypt=False) or {}
        except Exception:
            user_data = {}

        ok = auth._save_session(uid, cookie_str, user_data)
        if not ok:
            raise SystemExit("写入 Redis 失败：请检查 REDIS_URL 配置与 Redis 连接。")
        logger.info(f"已写入 Redis：netease:music:user:{uid}:cookie （有效期 7 天）")

    logger.info("完成。你现在可以运行 main.py/core.py 的任务逻辑，会优先使用这份 cookie。")


if __name__ == "__main__":
    main()


