"""
浏览器登录：自动密码登录 + 滑块识别 + 二次验证（二维码推到 Web）。
所有进度通过 event_bus 实时推给前端，同时写日志。

必须在 browser worker 线程内调用 login_account()。
"""

from __future__ import annotations

import os
import re
import random
import time
import urllib.parse
from typing import Optional

from playwright.sync_api import Frame, Page

from app.browser import selectors as S
from app.browser.helpers import (
    check_first,
    click_first,
    cookies_to_str,
    fill_first,
    has_login_cookie,
    scopes,
    try_click_if_visible,
)
from app.browser.manager import run_with_context
from app.config import DEBUG_DIR, DEBUG_SCREENSHOT
from app.event_bus import bus
from app.logging_conf import logger


class NetworkRiskError(RuntimeError):
    """页面提示网络环境安全风险时抛出。"""


def _emit(account_id: Optional[int], line: str, level: str = "info") -> None:
    """同时写日志 + 推送到 Web。"""
    if level == "error":
        logger.error(line)
    elif level == "warn":
        logger.warning(line)
    else:
        logger.info(line)
    bus.log(account_id, line, level=level)


def _debug_shot(page: Page | Frame, phone: str, tag: str, account_id: Optional[int] = None) -> None:
    """调试模式下把当前页面截图存到 DEBUG_DIR/{手机号}/，便于排查风控/滑块/二次验证。"""
    if not DEBUG_SCREENSHOT or not phone:
        return
    try:
        pw_page: Page = page if isinstance(page, Page) else page.page
        sub = re.sub(r"[^\d+]+", "_", str(phone).strip()).strip("_") or "unknown"
        out_dir = os.path.join(DEBUG_DIR, sub)
        os.makedirs(out_dir, exist_ok=True)
        safe_tag = re.sub(r"[^\w\-.]+", "_", tag).strip("_")[:60] or "shot"
        path = os.path.join(out_dir, f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_tag}.png")
        pw_page.screenshot(path=path, full_page=True)
        _emit(account_id, f"[调试] 已保存截图：{path}", "warn")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[调试] 截图失败：{e}")


# ---------- 网络风控检测 ----------
def _network_risk_visible(page: Page | Frame) -> bool:
    try:
        for scope in scopes(page):
            loc = scope.get_by_text(S.NETWORK_SECURITY_RISK_TEXT, exact=True)
            if loc.count() == 0:
                continue
            try:
                if loc.first.is_visible():
                    return True
            except Exception:
                return True
    except Exception:
        pass
    return False


def _ensure_no_network_risk(page: Page | Frame, account_id: Optional[int], where: str = "") -> None:
    if not _network_risk_visible(page):
        return
    _emit(account_id, f"[登录风控]（{where}）页面提示「{S.NETWORK_SECURITY_RISK_TEXT}」，请更换网络/关闭代理后重试", "error")
    raise NetworkRiskError(S.NETWORK_SECURITY_RISK_TEXT)


def _has_slider_modal(page: Page | Frame) -> bool:
    try:
        for scope in scopes(page):
            if scope.locator(S.SEL_YIDUN_MODAL).count() > 0:
                return True
    except Exception:
        pass
    return False


# ---------- 滑块验证码 ----------
def solve_slider(page: Page, account_id: Optional[int], max_retry: int = 3) -> None:
    """网易云 yidun 滑块：ddddocr 优先 + OpenCV 兜底 + 人类轨迹拖动。"""
    import cv2
    import numpy as np
    from ddddocr import DdddOcr

    def wait_real_image(scope, selector, min_width=120, timeout=10000):
        scope.wait_for_function(
            f"""() => {{
                const img = document.querySelector("{selector}");
                return img && img.complete && img.naturalWidth > {min_width};
            }}""",
            timeout=timeout,
        )

    def download_img(scope, selector) -> bytes:
        import requests
        from io import BytesIO
        from PIL import Image

        src = scope.locator(selector).first.get_attribute("src")
        if not src:
            raise RuntimeError("图片 src 为空")
        resp = requests.get(src, timeout=10)
        resp.raise_for_status()
        try:
            img = Image.open(BytesIO(resp.content))
            w, h = img.size
            if "bg-img" in selector and (w < 100 or h < 100):
                raise RuntimeError(f"背景图尺寸异常：{w}x{h}")
            if "jigsaw" in selector and (w < 30 or h < 30):
                raise RuntimeError(f"滑块图尺寸异常：{w}x{h}")
        except Exception as e:
            _emit(account_id, f"图片尺寸校验失败，自动刷新验证码：{e}", "warn")
            scope.locator(S.SEL_YIDUN_REFRESH).first.click()
            time.sleep(1)
            raise RuntimeError(f"图片无效，已刷新：{e}")
        return resp.content

    # 等验证码弹窗
    modal_found = False
    for _ in range(30):
        _ensure_no_network_risk(page, account_id, "等待滑块验证码期间")
        if _has_slider_modal(page):
            modal_found = True
            break
        time.sleep(0.3)

    if not modal_found:
        _ensure_no_network_risk(page, account_id, "确认无滑块弹窗前")
        _emit(account_id, "未触发验证码，跳过滑块验证")
        return

    ocr = DdddOcr(det=False, ocr=False, show_ad=False)

    for attempt in range(1, max_retry + 1):
        _emit(account_id, f"[滑块] 第 {attempt} 次尝试")
        for scope in scopes(page):
            try:
                wait_real_image(scope, S.SEL_YIDUN_BG)
                wait_real_image(scope, S.SEL_YIDUN_JIGSAW, min_width=40)

                bg_bytes = download_img(scope, S.SEL_YIDUN_BG)
                slider_bytes = download_img(scope, S.SEL_YIDUN_JIGSAW)
                if len(bg_bytes) < 5000 or len(slider_bytes) < 1000:
                    raise RuntimeError("验证码图片异常（过小）")

                bg_img = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
                slider_img = cv2.imdecode(np.frombuffer(slider_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
                if bg_img is None or slider_img is None:
                    raise RuntimeError("OpenCV 无法解码验证码图片")

                bg_h, bg_w = bg_img.shape[:2]
                slider_h, slider_w = slider_img.shape[:2]
                if slider_w > bg_w or slider_h > bg_h:
                    raise RuntimeError(f"滑块图尺寸超过背景图（{slider_w}x{slider_h} > {bg_w}x{bg_h}）")

                # ddddocr 优先（小图在前），失败回退 OpenCV
                try:
                    res = ocr.slide_match(slider_bytes, bg_bytes)
                    target_x = float(res["target"][0])
                    _emit(account_id, f"[滑块] ddddocr 识别位移：{target_x:.2f}px")
                except Exception as e:
                    _emit(account_id, f"[滑块] ddddocr 失败：{e}，改用 OpenCV", "warn")
                    result = cv2.matchTemplate(bg_img, slider_img, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)
                    target_x = float(max_loc[0])
                    _emit(account_id, f"[滑块] OpenCV 得分 {max_val:.4f}，位移 {target_x:.2f}px")

                # 小尺寸偏移修正
                target_x = target_x * 1.03 + 3.5

                slider = scope.locator(S.SEL_YIDUN_SLIDER).first
                box = slider.bounding_box()
                if not box:
                    raise RuntimeError("无法获取滑块位置")
                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2

                # 人类模拟拖动
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                total, cur = target_x, 0.0
                while cur < total:
                    step = min(total - cur, max(2, cur * 0.08))
                    cur += step
                    page.mouse.move(start_x + cur, start_y + (0.5 - time.time() % 1))
                    time.sleep(0.015)
                page.mouse.move(start_x + total - 2, start_y, steps=2)
                time.sleep(0.05)
                page.mouse.move(start_x + total, start_y, steps=2)
                page.mouse.up()
                time.sleep(2)

                if scope.locator(S.SEL_YIDUN_SLIDER).count() == 0:
                    _emit(account_id, "[滑块] 验证成功！")
                    return

                if attempt < max_retry:
                    _emit(account_id, f"[滑块] 第 {attempt} 次失败，刷新重试", "warn")
                    scope.locator(S.SEL_YIDUN_REFRESH).first.click()
                    time.sleep(2)
                    break
            except cv2.error as e:
                _emit(account_id, f"[滑块] OpenCV 处理失败：{e}", "warn")
                if attempt < max_retry:
                    time.sleep(1)
                continue
            except Exception as e:  # noqa: BLE001
                _emit(account_id, f"[滑块] 第 {attempt} 次尝试失败：{e}", "warn")
                continue

    _emit(account_id, "[滑块] 多次尝试后仍未通过", "warn")


# ---------- 二次验证（登录安全验证）----------
def check_secondary_verification(
    page: Page, account_id: Optional[int], *, timeout: int = 10, auto_action: bool = True
) -> bool:
    """
    检测登录安全验证弹窗。auto_action=True 时优先走「原设备扫码验证」，
    抓 pollingToken 生成二维码链接并推送到 Web + 通知渠道。
    返回 True 表示检测到弹窗（需要用户处理）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                modal = scope.locator(S.SEL_SECONDARY_MODAL)
                if modal.count() == 0:
                    continue

                _emit(account_id, "[二次验证] 检测到登录安全验证弹窗", "warn")
                if not auto_action:
                    return True

                options = scope.locator(S.SEL_SECONDARY_OPTION)
                count = options.count()
                if count == 0:
                    return True
                _emit(account_id, f"[二次验证] 发现 {count} 种验证方式")

                # 优先：原设备扫码验证
                for i in range(count):
                    try:
                        opt = options.nth(i)
                        txt = opt.locator(S.SEL_SECONDARY_OPTION_TEXT).first.inner_text(timeout=1000)
                        if "原设备扫码验证" in txt:
                            _emit(account_id, "[二次验证] 选择「原设备扫码验证」，抓取 pollingToken")
                            try:
                                with page.expect_response(
                                    lambda r: S.SCAN_APPLY_API in r.url, timeout=15000
                                ) as resp_info:
                                    opt.click()
                                payload = resp_info.value.json()
                                token = (payload or {}).get("data", {}).get("pollingToken")
                                if token:
                                    qr_uri = (
                                        "orpheus://rnpage?component=rn-account-verify&isTheme=true"
                                        "&immersiveMode=true&route=confirmOldDevice"
                                        f"&pollingToken={token}"
                                    )
                                    qr_url = "https://api.pwmqr.com/qrcode/create/?url=" + urllib.parse.quote(qr_uri, safe="")
                                    _emit(account_id, f"[二次验证] 扫码链接：{qr_url}", "warn")
                                    bus.qrcode(account_id, qr_url, tip="请用网易云音乐 App 扫码确认登录")
                                    _notify_qr(account_id, qr_url)
                                else:
                                    _emit(account_id, f"[二次验证] 未提取到 pollingToken：{payload}", "warn")
                            except Exception as e:  # noqa: BLE001
                                _emit(account_id, f"[二次验证] 监听扫码接口失败：{e}", "warn")
                                opt.click()
                            return True
                    except Exception:
                        continue

                # 其次：原设备确认
                for i in range(count):
                    try:
                        opt = options.nth(i)
                        txt = opt.locator(S.SEL_SECONDARY_OPTION_TEXT).first.inner_text(timeout=1000)
                        if "原设备确认" in txt:
                            _emit(account_id, "[二次验证] 尝试「原设备确认」")
                            opt.click()
                            time.sleep(2)
                            if scope.locator(S.SEL_SECONDARY_MODAL).count() == 0:
                                _emit(account_id, "[二次验证] 原设备确认成功")
                                return False
                            break
                    except Exception:
                        continue

                _emit(account_id, "[二次验证] 无法自动完成，请在弹窗中手动选择验证方式", "warn")
                return True
            except Exception:
                continue
        time.sleep(0.5)
    return False


def _notify_qr(account_id: Optional[int], qr_url: str) -> None:
    """扫码二维码走通知渠道（企业微信/自定义 webhook）。"""
    try:
        from app.notify import send_configured_notification

        send_configured_notification(
            f"账号(id={account_id}) 触发登录扫码验证，请尽快扫码：\n{qr_url}",
            title="网易音乐人登录扫码验证",
            event="login_qr",
            extra={"account_id": account_id, "qr_url": qr_url},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[二次验证] 扫码通知发送失败：{e}")


# ---------- 登录表单填写 ----------
def do_login_with_phone(page: Page, phone: str, password: str, account_id: Optional[int]) -> None:
    click_first(page, S.SEL_OTHER_LOGIN, exact_text=True)
    _emit(account_id, "已点击「选择其他登录模式」")
    check_first(page, S.SEL_TERMS)
    _emit(account_id, "已勾选协议")
    click_first(page, S.SEL_PHONE_ENTRY)
    _emit(account_id, "已点击「手机号登录/注册」")
    try:
        click_first(page, S.SEL_PWD_LOGIN, exact_text=True, timeout=20000)
    except Exception:
        click_first(page, f"text={S.SEL_PWD_LOGIN}", exact_text=False, timeout=20000)
    _emit(account_id, "已点击「密码登录」")
    time.sleep(random.uniform(0.2, 0.5))
    fill_first(page, S.SEL_PHONE_INPUT, phone)
    time.sleep(random.uniform(0.2, 0.5))
    fill_first(page, S.SEL_PWD_INPUT, password)
    time.sleep(random.uniform(0.2, 0.5))
    click_first(page, S.SEL_LOGIN_BTN)
    _emit(account_id, "已点击「登录」")


# ---------- 提取 uid / 昵称 ----------
def _fetch_user_info(page: Page, account_id: Optional[int]) -> tuple[Optional[str], Optional[str]]:
    """
    登录成功后在页面同源上下文请求账号信息，拿 uid 和昵称。
    走浏览器 fetch（携带 cookie、同源），规避 requests 通道风控。
    """
    try:
        result = page.evaluate(
            """async () => {
                const r = await fetch('/api/nuser/account/get', {
                    method: 'GET', credentials: 'include'
                });
                return await r.json();
            }"""
        )
        uid = None
        nickname = None
        if isinstance(result, dict):
            profile = result.get("profile") or {}
            account = result.get("account") or {}
            if isinstance(profile, dict):
                uid = profile.get("userId")
                nickname = profile.get("nickname")
            if uid is None and isinstance(account, dict):
                uid = account.get("id")
        if uid is not None:
            uid = str(uid)
            _emit(account_id, f"已获取账号信息：uid={uid}，昵称={nickname or '-'}")
        else:
            _emit(account_id, "未能解析到 uid（账号信息接口返回异常）", "warn")
        return uid, nickname
    except Exception as e:  # noqa: BLE001
        _emit(account_id, f"获取账号信息失败：{e}", "warn")
        return None, None


# ---------- 主流程 ----------
def login_account(profile_dir: str, phone: str, password: str, account_id: Optional[int] = None) -> dict:
    """
    执行完整登录流程。返回 {ok, cookie_str, message}。
    必须在 browser worker 线程内调用。
    """
    bus.status(account_id, "logging_in", "开始登录")
    _emit(account_id, f"使用 Playwright 打开登录页，账号：{phone}")

    with run_with_context(profile_dir, account_id=account_id, label="登录") as (context, page):
        # 先看持久化 profile 是否已是登录态
        try:
            existing = context.cookies("https://music.163.com")
            if has_login_cookie(existing):
                _emit(account_id, "检测到持久化 profile 已是登录态，跳过密码登录")
                cookie_str = cookies_to_str(existing)
                uid, nickname = _fetch_user_info(page, account_id)
                bus.status(account_id, "login_ok", "已登录（复用会话）")
                return {"ok": True, "cookie_str": cookie_str, "uid": uid, "nickname": nickname, "message": "reuse session"}
        except Exception:
            pass

        page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
        _emit(account_id, "开始执行自动登录流程")

        try:
            do_login_with_phone(page, phone, password, account_id)
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"登录表单填写异常：{e}", "error")
            _debug_shot(page, phone, "login_flow_error", account_id)
            raise

        # 滑块
        try:
            solve_slider(page, account_id)
        except NetworkRiskError:
            _debug_shot(page, phone, "network_risk_slider", account_id)
            bus.status(account_id, "login_fail", "网络环境风险")
            return {"ok": False, "cookie_str": "", "message": "network risk"}
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"滑块处理异常：{e}", "warn")
            _debug_shot(page, phone, "slider_exception", account_id)

        # 滑块成功后可能回到「密码登录」选项卡，重试最多 3 次
        for _ in range(3):
            time.sleep(1)
            if not try_click_if_visible(page, "密码登录", exact_text=True, timeout_ms=2500):
                break
            _emit(account_id, "[登录] 密码登录选项卡再次出现，重新输入")
            time.sleep(random.uniform(0.2, 0.5))
            fill_first(page, S.SEL_PHONE_INPUT, phone)
            time.sleep(random.uniform(0.2, 0.5))
            fill_first(page, S.SEL_PWD_INPUT, password)
            time.sleep(random.uniform(0.2, 0.5))
            click_first(page, S.SEL_LOGIN_BTN, timeout=10000)
            if _has_slider_modal(page):
                try:
                    solve_slider(page, account_id)
                except NetworkRiskError:
                    bus.status(account_id, "login_fail", "网络环境风险")
                    return {"ok": False, "cookie_str": "", "message": "network risk"}
                except Exception as e:  # noqa: BLE001
                    _emit(account_id, f"滑块处理异常：{e}", "warn")

        try:
            _ensure_no_network_risk(page, account_id, "登录重试结束后")
        except NetworkRiskError:
            _debug_shot(page, phone, "network_risk", account_id)
            bus.status(account_id, "login_fail", "网络环境风险")
            return {"ok": False, "cookie_str": "", "message": "network risk"}

        # 二次验证
        try:
            if check_secondary_verification(page, account_id, timeout=10):
                _emit(account_id, "[登录] 需要二次验证，等待完成...", "warn")
                bus.status(account_id, "secondary", "等待二次验证/扫码")
                deadline = time.time() + 180
                while time.time() < deadline:
                    if not check_secondary_verification(page, account_id, timeout=2, auto_action=False):
                        _emit(account_id, "[登录] 二次验证已完成")
                        break
                    time.sleep(3)
                else:
                    _emit(account_id, "[登录] 二次验证等待超时", "warn")
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"检查二次验证出错：{e}", "warn")

        # 等待登录 cookie
        cookie_str, ok = "", False
        deadline = time.time() + 60
        while time.time() < deadline:
            cookies = context.cookies("https://music.163.com")
            cookie_str = cookies_to_str(cookies)
            if has_login_cookie(cookies):
                ok = True
                break
            time.sleep(1)

        if ok:
            _emit(account_id, "登录成功，已获取有效 Cookie")
            uid, nickname = _fetch_user_info(page, account_id)
            bus.status(account_id, "login_ok", "登录成功")
            return {"ok": True, "cookie_str": cookie_str, "uid": uid, "nickname": nickname, "message": "ok"}

        _emit(account_id, "登录未获取到有效 Cookie", "error")
        _debug_shot(page, phone, "no_login_cookie", account_id)
        bus.status(account_id, "login_fail", "未获取到有效 Cookie")
        return {"ok": False, "cookie_str": cookie_str, "message": "no login cookie"}
