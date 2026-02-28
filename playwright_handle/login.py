"""
使用 Playwright 打开网易云音乐 **手机号密码登录页**，自动完成你描述的所有点击和输入，
并把登录后的 Cookie 保存到 Redis（供 core.py/main.py 复用）。

使用前先在文件最上面改成你自己的手机号、密码、可选 uid。
"""

from __future__ import annotations

import os
import random
import time
import logging
import urllib.parse
from typing import Optional

from ddddocr import DdddOcr
from playwright.sync_api import sync_playwright, Page, Frame

from core import NeteaseClient  # 仅用于本模块内部根据 Cookie 识别 uid

logger = logging.getLogger("netease_music")


# ======== 按需修改这里（作为脚本单独运行时使用） ========
LOGIN_URL = "https://music.163.com/#/login?targetUrl=https%3A%2F%2Fmusic.163.com%2Fst%2Fmusician"

# 作为脚本直接运行时的默认账号（集成到 main.py 时会传参覆盖）
PHONE = input("手机号：")
PASSWORD = input("密码：")

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


def _try_click_if_visible(page: Page | Frame, text: str, *, exact_text: bool = True, timeout_ms: int = 3000) -> bool:
    """
    若在 timeout 内找到可点击的文本则点击并返回 True，否则不抛错、返回 False。
    用于「有则点一下」的场景（如滑块成功后可能再次出现「密码登录」选项卡）。
    """
    deadline = time.time() + max(0.5, timeout_ms / 1000)
    while time.time() < deadline:
        for scope in _scopes(page):
            try:
                if exact_text:
                    loc = scope.get_by_text(text, exact=True)
                else:
                    loc = scope.locator(f"text={text}")
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=500)
                loc.first.click()
                return True
            except Exception:
                continue
        time.sleep(0.2)
    return False


def _has_yidun_slider_modal(page: Page | Frame) -> bool:
    """
    快速判断是否出现 yidun 滑块弹窗/容器（不等待，只做存在性检测）。
    用于避免重复调用 solve_slider_captcha() 造成多次“未触发验证码”的噪音与耗时。
    """
    try:
        for scope in _scopes(page):
            if scope.locator(".yidun_modal__body, .yidun.yidun-custom").count() > 0:
                return True
    except Exception:
        return False
    return False


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
    网易云 yidun 滑块高成功率版本（修复 OpenCV 尺寸断言错误）
    - 真滑块判断（naturalWidth）
    - 直接下载原图（非 screenshot）
    - 小尺寸偏移修正
    - 人类拖动轨迹
    - 新增尺寸校验：确保滑块图 ≤ 背景图（解决 OpenCV 断言错误）
    """

    def wait_real_image(scope, selector, min_width=120, timeout=10000):
        scope.wait_for_function(
            f"""
            () => {{
                const img = document.querySelector("{selector}");
                return img && img.complete && img.naturalWidth > {min_width};
            }}
            """,
            timeout=timeout
        )

    def download_img(scope, selector) -> bytes:
        src = scope.locator(selector).first.get_attribute("src")
        if not src:
            raise RuntimeError("图片 src 为空")
        import requests
        # 1. 下载图片并解析尺寸
        resp = requests.get(src, timeout=10)
        resp.raise_for_status()
        # 2. 用PIL校验图片实际尺寸（避免naturalWidth欺骗）
        from io import BytesIO
        from PIL import Image
        try:
            img = Image.open(BytesIO(resp.content))
            img_width, img_height = img.size
            # 3. 强制校验：背景图≥100x100，滑块图≥30x30（根据网易云实际情况调整）
            if "bg-img" in selector and (img_width < 100 or img_height < 100):
                raise RuntimeError(f"背景图尺寸异常：{img_width}x{img_height}")
            if "jigsaw" in selector and (img_width < 30 or img_height < 30):
                raise RuntimeError(f"滑块图尺寸异常：{img_width}x{img_height}")
        except Exception as e:
            # 4. 尺寸异常：自动点击刷新按钮，并重抛异常触发重试
            logger.warning(f"图片尺寸校验失败，自动刷新验证码：{e}")
            scope.locator(".yidun_refresh").first.click()
            time.sleep(1)
            raise RuntimeError(f"图片无效，已刷新：{e}")
        return resp.content

    def is_sms_mode(scope):
        return scope.locator(".yidun_smsbox, .yidun_voice").count() > 0

    # 等验证码弹窗
    modal_found = False
    for _ in range(30):
        for scope in _scopes(page):
            if scope.locator(".yidun_modal__body, .yidun.yidun-custom").count() > 0:
                modal_found = True
                break
        if modal_found:
            break
        time.sleep(0.3)

    if not modal_found:
        logger.info("未触发验证码，跳过滑块验证")
        return

    # 优先使用 ddddocr 的 slide_match（修正参数顺序 + 尺寸校验，避免其内部 OpenCV 断言）
    # 同时保留 OpenCV 匹配作为兜底方案
    ocr = DdddOcr(det=False, ocr=False, show_ad=False)
    import cv2
    import numpy as np

    for attempt in range(1, max_retry + 1):
        logger.info(f"[滑块] 第 {attempt} 次尝试")

        for scope in _scopes(page):
            try:
                # 等待真实图片加载（避免加载占位图）
                wait_real_image(scope, "img.yidun_bg-img")
                wait_real_image(scope, "img.yidun_jigsaw", min_width=40)

                # 下载背景图和滑块图
                bg_bytes = download_img(scope, "img.yidun_bg-img")
                slider_bytes = download_img(scope, "img.yidun_jigsaw")

                # 校验图片有效性
                if len(bg_bytes) < 5000 or len(slider_bytes) < 1000:
                    raise RuntimeError(f"图片异常（背景图大小：{len(bg_bytes)}，滑块图大小：{len(slider_bytes)}）")

                # ========== 核心修复：尺寸校验 + ddddocr 优先，OpenCV 匹配兜底 ==========
                # 将字节流转换为 OpenCV 灰度图像
                bg_img = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
                slider_img = cv2.imdecode(np.frombuffer(slider_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)

                if bg_img is None or slider_img is None:
                    raise RuntimeError("OpenCV 无法解码验证码图片")

                # 获取图片尺寸（高, 宽）
                bg_h, bg_w = bg_img.shape[:2]
                slider_h, slider_w = slider_img.shape[:2]
                logger.info(f"[滑块] 图片尺寸 - 背景图：{bg_w}x{bg_h}，滑块图：{slider_w}x{slider_h}")

                # 确保模板（滑块）不会比背景图大，避免 ddddocr / OpenCV matchTemplate 尺寸断言错误
                if slider_w > bg_w or slider_h > bg_h:
                    raise RuntimeError(
                        f"滑块图尺寸超过背景图（{slider_w}x{slider_h} > {bg_w}x{bg_h}），跳过本次匹配"
                    )

                # 优先使用 ddddocr 的 slide_match：
                # 关键点：按照“小图在前、背景在后”的顺序传参，避免其内部 OpenCV 尺寸断言
                try:
                    res = ocr.slide_match(slider_bytes, bg_bytes)
                    target_x = float(res["target"][0])
                    logger.info(f"[滑块] ddddocr 识别位移：{target_x:.2f} 像素")
                except Exception as e:
                    logger.warning(f"[滑块] ddddocr.slide_match 失败：{e}，改用 OpenCV 匹配")
                    # 使用 OpenCV 模板匹配查找滑块在背景图中的 X 方向偏移
                    result = cv2.matchTemplate(bg_img, slider_img, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)
                    target_x = max_loc[0]
                    logger.info(f"[滑块] OpenCV 匹配得分：{max_val:.4f}，原始位移：{target_x:.2f} 像素")

                # 小尺寸 yidun 偏移修正（关键）
                target_x *= 1.03
                target_x += 3.5
                logger.info(f"[滑块] 修正后位移：{target_x:.2f} 像素")

                # 获取滑块位置，准备拖动
                slider = scope.locator(".yidun_slider__icon").first
                box = slider.bounding_box()
                if not box:
                    raise RuntimeError("无法获取滑块位置，跳过本次拖动")

                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2

                # 人类模拟拖动轨迹（避免被风控）
                page.mouse.move(start_x, start_y)
                page.mouse.down()

                total = target_x
                cur = 0
                while cur < total:
                    step = min(total - cur, max(2, cur * 0.08))  # 先慢后快
                    cur += step
                    # 轻微上下抖动，模拟人类操作
                    page.mouse.move(start_x + cur, start_y + (0.5 - time.time() % 1))
                    time.sleep(0.015)

                # 轻微回拉（反机器人风控）
                page.mouse.move(start_x + total - 2, start_y, steps=2)
                time.sleep(0.05)
                page.mouse.move(start_x + total, start_y, steps=2)

                page.mouse.up()
                time.sleep(2)  # 等待验证结果

                # 验证成功判断：滑块元素消失
                if scope.locator(".yidun_slider__icon").count() == 0:
                    logger.info("[滑块] 验证码验证成功！")
                    return

                # 验证失败，刷新验证码后重试：必须 break 出内层 scope 循环，下一轮 attempt 再重新扫 scope 等新图
                if attempt < max_retry:
                    logger.info(f"[滑块] 第 {attempt} 次失败，刷新验证码重试")
                    scope.locator(".yidun_refresh").first.click()
                    time.sleep(2)  # 等新验证码 DOM 与图片加载
                    break  # 跳出 for scope，进入下一 attempt，重新从第一个 scope 开始等新图
            except cv2.error as e:
                # 捕获 OpenCV 相关错误，单独兜底
                logger.warning(f"[滑块] OpenCV 处理失败：{str(e)}，跳过本次尝试")
                if attempt < max_retry:
                    time.sleep(1)
                continue
            except Exception as e:
                # 捕获其他所有异常，避免流程中断
                logger.warning(f"[滑块] 第 {attempt} 次尝试失败：{str(e)}")
                continue

    logger.error(f"[滑块] 累计 {max_retry} 次尝试均失败，放弃滑块验证（请手动完成或检查网络/验证码样式）")


def check_secondary_verification(page: Page | Frame, timeout: int = 10, *, auto_action: bool = True) -> bool:
    """
    检查是否需要二次验证（登录安全验证弹窗）。
    如果出现二次验证弹窗，记录日志并返回 True。
    
    返回:
        True: 检测到二次验证弹窗
        False: 未检测到二次验证弹窗
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for scope in _scopes(page):
            try:
                modal = scope.locator(".mrc-modal-container")
                title = scope.get_by_text("登录安全验证", exact=False)
                
                # 有时弹窗标题文案会变化，不能强依赖 title；只要容器存在就认为进入二次验证流程
                if modal.count() > 0:
                    logger.warning("[二次验证] 检测到登录安全验证弹窗，需要额外验证")
                    pw_page: Page = page if isinstance(page, Page) else page.page

                    if not auto_action:
                        return True
                    
                    # 检查可用的验证方式
                    verification_options = scope.locator(".mjZhxAab")
                    option_count = verification_options.count()
                    
                    if option_count > 0:
                        logger.info(f"[二次验证] 发现 {option_count} 种验证方式")

                        # 优先：原设备扫码验证（可生成二维码供手机扫码确认）
                        for i in range(option_count):
                            try:
                                option = verification_options.nth(i)
                                option_text = option.locator("span.DwyRKeOe").first.inner_text(timeout=1000)
                                if "原设备扫码验证" in option_text:
                                    logger.info("[二次验证] 尝试点击「原设备扫码验证」并抓取 pollingToken")
                                    try:
                                        with pw_page.expect_response(
                                            lambda r: "/weapi/login/origin-device/scan-apply/start" in r.url,
                                            timeout=15000,
                                        ) as resp_info:
                                            option.click()
                                        resp = resp_info.value
                                        payload = resp.json()
                                        polling_token = (
                                            (payload or {})
                                            .get("data", {})
                                            .get("pollingToken")
                                        )
                                        if polling_token:
                                            qr_uri = (
                                                "orpheus://rnpage?"
                                                "component=rn-account-verify&isTheme=true&immersiveMode=true&route=confirmOldDevice"
                                                f"&pollingToken={polling_token}"
                                            )
                                            qr_url = (
                                                "https://api.pwmqr.com/qrcode/create/?url="
                                                + urllib.parse.quote(qr_uri, safe="")
                                            )
                                            logger.warning(f"[二次验证] 扫码二维码链接：{qr_url}")
                                            # 标记：已进入扫码验证流程，后续应至少等待一段时间给用户扫码
                                            try:
                                                setattr(pw_page, "_secondary_scan_started_at", time.time())
                                            except Exception:
                                                pass
                                        else:
                                            logger.warning(f"[二次验证] 未从接口返回中提取到 pollingToken：{payload}")
                                    except Exception as e:
                                        logger.warning(f"[二次验证] 监听 scan-apply/start 接口失败：{e}")
                                        option.click()
                                    return True
                            except Exception:
                                continue

                        # 其次：原设备确认（有些情况还需要再输入验证码，成功率不如扫码）
                        for i in range(option_count):
                            try:
                                option = verification_options.nth(i)
                                option_text = option.locator("span.DwyRKeOe").first.inner_text(timeout=1000)
                                if "原设备确认" in option_text:
                                    logger.info("[二次验证] 尝试点击「原设备确认」")
                                    option.click()
                                    time.sleep(2)
                                    # 检查弹窗是否消失
                                    if scope.locator(".mrc-modal-container").count() == 0:
                                        logger.info("[二次验证] 原设备确认成功，弹窗已关闭")
                                        return False
                                    break
                            except Exception:
                                continue
                        
                        # 如果自动处理失败，记录需要手动处理
                        logger.warning(
                            "[二次验证] 无法自动完成二次验证，请手动选择验证方式：\n"
                            "  - 短信验证\n"
                            "  - 原设备确认\n"
                            "  - 原设备扫码验证\n"
                            "  - 微信授权验证"
                        )
                        return True
                    
            except Exception:
                continue
        
        time.sleep(0.5)
    
    logger.debug("[二次验证] 未检测到二次验证弹窗")
    return False


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
    time.sleep(random.uniform(0.2, 0.5))

    # 5. 输入手机号
    _fill_first(page, "input[placeholder='请输入手机号']", phone)
    logger.info("已输入手机号")
    time.sleep(random.uniform(0.2, 0.5))

    # 6. 输入密码
    _fill_first(page, "input[placeholder='请输入密码']", password)
    logger.info("已输入密码")
    time.sleep(random.uniform(0.2, 0.5))

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
            headless=True,
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

        # 少数情况下：滑块成功后会回到「密码登录」选项卡，需要重新点并再次触发滑块
        # 这里避免无条件重复 solve_slider_captcha()，否则会出现多次“未触发验证码”的日志与耗时
        for _ in range(3):
            time.sleep(1)
            if not _try_click_if_visible(page, "密码登录", exact_text=True, timeout_ms=2500):
                break
            logger.info("[登录] 检测到密码登录选项卡再次出现，已重新点击「密码登录」，继续执行输入与登录")
            time.sleep(random.uniform(0.2, 0.5))
            _fill_first(page, "input[placeholder='请输入手机号']", phone)
            time.sleep(random.uniform(0.2, 0.5))
            _fill_first(page, "input[placeholder='请输入密码']", password)
            time.sleep(random.uniform(0.2, 0.5))
            _click_first(page, "a:has(div:has-text('登录'))", timeout=10000)
            time.sleep(random.uniform(0.2, 0.5))
            logger.info("[登录] 已重新输入账号密码并点击登录")

            # 只有检测到滑块容器时才处理滑块
            if _has_yidun_slider_modal(page):
                try:
                    solve_slider_captcha(page)
                except Exception as e:
                    logger.warning(f"滑块验证码处理过程出错：{e}")

        # 滑块验证完成后，检查是否需要二次验证
        try:
            needs_secondary = check_secondary_verification(page, timeout=10)
            if needs_secondary:
                logger.warning("[登录] 检测到需要二次验证，等待用户手动完成...")
                # 扫码验证：最多等 60 秒，每 5 秒检查一次；用户提前完成就立刻继续
                try:
                    started_at = getattr(page, "_secondary_scan_started_at", None)
                except Exception:
                    started_at = None
                if started_at:
                    logger.warning("[登录] 已生成扫码二维码链接，开始轮询等待（每 5 秒检查一次，最多 60 秒）...")
                    scan_deadline = time.time() + 60
                    while time.time() < scan_deadline:
                        # 被动检测：不重复点击/不重复生成二维码
                        still_needs_scan = check_secondary_verification(page, timeout=2, auto_action=False)
                        if not still_needs_scan:
                            logger.info("[登录] 二次验证已完成（扫码），继续登录流程")
                            break
                        time.sleep(5)

                # 循环检查，最多等待 120 秒，直到二次验证弹窗消失（被动检测）
                secondary_deadline = time.time() + 120
                while time.time() < secondary_deadline:
                    still_needs = check_secondary_verification(page, timeout=2, auto_action=False)
                    if not still_needs:
                        logger.info("[登录] 二次验证已完成，继续登录流程")
                        break
                    time.sleep(2)
                else:
                    logger.warning("[登录] 二次验证等待超时，继续尝试获取 Cookie")
        except Exception as e:
            logger.warning(f"检查二次验证时出错：{e}")

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


