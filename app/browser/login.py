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
    fetch_session_user,
    has_login_cookie,
    scopes,
    try_click_if_visible,
)
from app.browser.manager import run_with_context
from app import repository as repo
from app.account_identity import account_label, mask_phone
from app.config import DEBUG_DIR, DEBUG_SCREENSHOT, LOGIN_METHOD, LOGIN_METHODS
from app.event_bus import bus
from app.logging_conf import logger


SECONDARY_WAIT_SECONDS = 180  # 二次验证总等待时长
QR_RETRY_INTERVAL = 15  # 二维码迟迟拿不到时，每隔多少秒重试抓取一次
QRCODE_LOGIN_TIMEOUT = 300  # 扫码登录等待用户扫码的时长
QRCODE_MAX_RELOAD = 3  # 扫码登录期间最多重新加载登录页的次数
CONFIRM_WAIT_SECONDS = 60  # 等待服务端确认登录态的时长


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
        display_path = os.path.join(DEBUG_DIR, mask_phone(phone), os.path.basename(path))
        _emit(account_id, f"[调试] 已保存截图：{display_path}", "warn")
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
def solve_slider(page: Page, account_id: Optional[int], max_retry: int = 3) -> bool:
    """网易云 yidun 滑块：ddddocr 优先 + OpenCV 兜底 + 人类轨迹拖动。

    返回 True 表示已通过或本来就没弹验证码，False 表示多次尝试后仍未通过
    （供上层决定是否降级到扫码登录）。
    """
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
        return True

    ocr = DdddOcr(det=False, ocr=False, show_ad=False)

    for attempt in range(1, max_retry + 1):
        _emit(account_id, f"[滑块] 第 {attempt} 次尝试")
        for scope in scopes(page):
            try:
                # 先用不带等待的 count() 排除掉不含验证码的 frame。
                # 否则 wait_for_function 会在每个无关 frame 上死等满 timeout。
                if scope.locator(S.SEL_YIDUN_BG).count() == 0:
                    continue
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
                    return True

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
    return False


# ---------- 二次验证（登录安全验证）----------
def new_qr_state() -> dict:
    """跨多次 check_secondary_verification 调用共享的二次验证状态。"""
    return {"token": None, "pushed": False, "token_pushed": None, "modal_logged": False}


def make_scan_response_hook(qr_state: dict):
    """常驻 response 监听器：scan-apply 接口一出现就把 pollingToken 缓存下来。

    原实现只在单次点击时用 expect_response 抓取，一旦错过那个窗口（接口早于监听
    到达、超时、或弹窗结构变化导致点击没落在预期元素上），token 就永久丢失，
    弹窗会一直停在原地不动（issue #22）。常驻监听彻底消除这个竞态。
    """

    def _hook(resp) -> None:
        try:
            if S.SCAN_APPLY_API not in resp.url:
                return
            token = ((resp.json() or {}).get("data") or {}).get("pollingToken")
            if token:
                qr_state["token"] = token
        except Exception:  # noqa: BLE001  监听器内异常绝不能影响主流程
            pass

    return _hook


def _push_scan_qr(account_id: Optional[int], token: str, qr_state: Optional[dict] = None) -> bool:
    """由 pollingToken 生成二维码并推送到 Web 界面 + 通知渠道。"""
    if not token:
        return False
    if qr_state is not None and qr_state.get("token_pushed") == token:
        return True  # 同一个 token 不重复推送
    qr_uri = (
        "orpheus://rnpage?component=rn-account-verify&isTheme=true"
        "&immersiveMode=true&route=confirmOldDevice"
        f"&pollingToken={token}"
    )
    qr_url = "https://api.pwmqr.com/qrcode/create/?url=" + urllib.parse.quote(qr_uri, safe="")
    _emit(account_id, f"[二次验证] 扫码链接：{qr_url}", "warn")
    bus.qrcode(account_id, qr_url, tip="请用网易云音乐 App 扫码确认登录")
    _notify_qr(account_id, qr_url)
    if qr_state is not None:
        qr_state["pushed"] = True
        qr_state["token_pushed"] = token
    return True


def check_secondary_verification(
    page: Page,
    account_id: Optional[int],
    *,
    timeout: int = 10,
    auto_action: bool = True,
    qr_state: Optional[dict] = None,
) -> bool:
    """
    检测登录安全验证弹窗。auto_action=True 时优先走「原设备扫码验证」，
    抓 pollingToken 生成二维码链接并推送到 Web + 通知渠道。
    返回 True 表示检测到弹窗（需要用户处理）。

    qr_state 用于跨多次调用记录「二维码是否已推送」与「弹窗是否已播报」，
    使等待循环可以重试抓取二维码，同时避免每轮轮询都重复打印同一条日志。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for scope in scopes(page):
            try:
                modal = scope.locator(S.SEL_SECONDARY_MODAL)
                if modal.count() == 0:
                    continue

                # 只在状态跳变时播报，避免 3 秒一条刷屏（issue #22 的日志表现）
                if qr_state is None or not qr_state.get("modal_logged"):
                    _emit(account_id, "[二次验证] 检测到登录安全验证弹窗", "warn")
                    if qr_state is not None:
                        qr_state["modal_logged"] = True

                # 常驻监听器可能已经抓到 token，先兑现掉
                if qr_state is not None and qr_state.get("token") and not qr_state.get("pushed"):
                    _push_scan_qr(account_id, qr_state["token"], qr_state)

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
                            token = None
                            try:
                                with page.expect_response(
                                    lambda r: S.SCAN_APPLY_API in r.url, timeout=15000
                                ) as resp_info:
                                    opt.click()
                                payload = resp_info.value.json()
                                token = ((payload or {}).get("data") or {}).get("pollingToken")
                                if not token:
                                    _emit(account_id, f"[二次验证] 未提取到 pollingToken：{payload}", "warn")
                            except Exception as e:  # noqa: BLE001
                                _emit(account_id, f"[二次验证] 监听扫码接口失败：{e}", "warn")
                            # 兜底：常驻监听器可能已经抓到 token
                            if not token and qr_state is not None:
                                token = qr_state.get("token")
                            if token:
                                _push_scan_qr(account_id, token, qr_state)
                            else:
                                _emit(account_id, "[二次验证] 本轮未获得二维码，稍后自动重试", "warn")
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


def _notify_qr(account_id: Optional[int], qr_url: str, *, message: Optional[str] = None) -> None:
    """扫码二维码走通知渠道（企业微信/自定义 webhook）。"""
    try:
        from app.notify import send_configured_notification

        identity = account_label(account_id)
        send_configured_notification(
            message or f"账号 {identity} 触发登录扫码验证，请尽快扫码：\n{qr_url}",
            title="网易音乐人登录扫码验证",
            event="login_qr",
            extra={"account": identity, "qr_url": qr_url},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[二次验证] 扫码通知发送失败：{e}")


# ---------- 扫码登录 ----------
def _grab_qr_data_uri(page: Page) -> Optional[str]:
    """把登录页里的二维码导出成 data URI（二维码画布在子 iframe 内）。"""
    for scope in scopes(page):
        try:
            uri = scope.evaluate(S.QR_EXTRACT_SCRIPT)
        except Exception:  # noqa: BLE001  frame 可能正在导航
            continue
        if uri:
            return uri
    return None


def login_with_qrcode(
    page: Page,
    account_id: Optional[int],
    phone: str,
    *,
    timeout: int = QRCODE_LOGIN_TIMEOUT,
) -> bool:
    """扫码登录。返回 True 表示服务端已确认登录成功。

    网易云 web 登录页的默认视图就是扫码登录，且全程不触发易盾滑块。二维码由页面
    自行渲染在子 iframe 的 <canvas> 上，这里用 canvas.toDataURL() 导出成 data URI
    直接交给前端 <img> 显示。因此这条路径既不需要打码/轨迹模拟，也不需要新增
    二维码依赖或第三方二维码服务。
    """
    _emit(account_id, "[扫码登录] 打开登录页，等待二维码生成")
    page.goto(S.LOGIN_URL, wait_until="domcontentloaded")

    deadline = time.time() + timeout
    last_src: Optional[str] = None
    misses = 0
    reloads = 0
    while time.time() < deadline:
        _ensure_no_network_risk(page, account_id, "扫码登录期间")

        # 服务端确认登录态优先于一切
        cookies = page.context.cookies("https://music.163.com")
        if has_login_cookie(cookies):
            uid, nickname, _error = fetch_session_user(page, S.MUSICIAN_HOME_URL)
            if uid:
                _emit(account_id, f"[扫码登录] 扫码成功：uid={uid}，昵称={nickname or '-'}")
                return True

        src = _grab_qr_data_uri(page)
        if src:
            misses = 0
            if src != last_src:
                last_src = src
                # data URI 直接推给前端 <img>，不经任何第三方服务
                bus.qrcode(account_id, src, tip="请用网易云音乐 App 扫码登录")
                _notify_qr(
                    account_id,
                    "",
                    message=(
                        f"账号 {account_label(account_id, phone=phone)} 需要扫码登录，"
                        "请打开管理页面用网易云音乐 App 扫码"
                    ),
                )
                _emit(account_id, "[扫码登录] 二维码已推送到网页，请用网易云音乐 App 扫码", "warn")
        else:
            misses += 1
            # 二维码过期或页面被跳转：重新加载登录页再取一次
            if misses >= 5 and reloads < QRCODE_MAX_RELOAD:
                reloads += 1
                misses = 0
                last_src = None
                _emit(account_id, f"[扫码登录] 未取到二维码，重新加载登录页（第 {reloads} 次）", "warn")
                page.goto(S.LOGIN_URL, wait_until="domcontentloaded")

        time.sleep(2)

    _emit(account_id, f"[扫码登录] {timeout} 秒内未完成扫码", "warn")
    _debug_shot(page, phone, "qrcode_timeout", account_id)
    return False


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
    uid, nickname, error = fetch_session_user(page, S.MUSICIAN_HOME_URL)
    if uid:
        _emit(account_id, f"已获取账号信息：uid={uid}，昵称={nickname or '-'}")
    else:
        _emit(account_id, f"服务端登录态校验失败：{error or '未能解析 uid'}", "warn")
    return uid, nickname


# ---------- 主流程 ----------
def login_account(profile_dir: str, phone: str, password: str, account_id: Optional[int] = None) -> dict:
    """
    执行完整登录流程。返回 {ok, cookie_str, message}。
    必须在 browser worker 线程内调用。
    """
    bus.status(account_id, "logging_in", "开始登录")
    _emit(account_id, f"使用 Playwright 打开登录页，账号：{account_label(account_id, phone=phone)}")

    with run_with_context(profile_dir, account_id=account_id, label="登录") as (context, page):
        # 全程监听扫码验证接口，避免只在单次点击窗口内抓 token（issue #22）
        qr_state = new_qr_state()
        page.on("response", make_scan_response_hook(qr_state))

        # 先看持久化 profile 是否已是登录态
        try:
            existing = context.cookies("https://music.163.com")
            if has_login_cookie(existing):
                _emit(account_id, "检测到持久化 profile 含登录 cookie，正在向服务端校验...")
                uid, nickname = _fetch_user_info(page, account_id)
                if uid:
                    cookie_str = cookies_to_str(existing)
                    _emit(account_id, "服务端确认会话有效，跳过密码登录")
                    bus.status(account_id, "login_ok", "已登录（复用会话）")
                    return {"ok": True, "cookie_str": cookie_str, "uid": uid, "nickname": nickname, "message": "reuse session"}
                _emit(account_id, "持久化 cookie 已被服务端判定失效，继续执行密码登录", "warn")
        except Exception:
            pass

        # 登录方式：auto（密码优先，失败自动转扫码）/ password / qrcode
        method = (repo.get_setting("login_method", LOGIN_METHOD) or LOGIN_METHOD).strip().lower()
        if method not in LOGIN_METHODS:
            method = "auto"
        use_qr = method == "qrcode"
        fallback_reason = ""
        if use_qr:
            _emit(account_id, "登录方式为「扫码登录」，跳过密码登录")

        if not use_qr:
            page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
            _emit(account_id, "开始执行自动登录流程")

            try:
                do_login_with_phone(page, phone, password, account_id)
            except Exception as e:  # noqa: BLE001
                _emit(account_id, f"登录表单填写异常：{e}", "error")
                _debug_shot(page, phone, "login_flow_error", account_id)
                if method != "auto":
                    raise
                fallback_reason = f"登录表单填写异常：{e}"
                use_qr = True

        # 滑块
        if not use_qr:
            try:
                if not solve_slider(page, account_id) and method == "auto":
                    fallback_reason = "滑块验证多次未通过"
                    use_qr = True
            except NetworkRiskError:
                _debug_shot(page, phone, "network_risk_slider", account_id)
                bus.status(account_id, "login_fail", "网络环境风险")
                return {"ok": False, "cookie_str": "", "message": "network risk"}
            except Exception as e:  # noqa: BLE001
                _emit(account_id, f"滑块处理异常：{e}", "warn")
                _debug_shot(page, phone, "slider_exception", account_id)

        # 滑块成功后可能回到「密码登录」选项卡，重试最多 3 次
        for _ in range(0 if use_qr else 3):
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
            if not use_qr and check_secondary_verification(page, account_id, timeout=10, qr_state=qr_state):
                _emit(account_id, "[登录] 需要二次验证，等待完成...", "warn")
                bus.status(account_id, "secondary", "等待二次验证/扫码")
                deadline = time.time() + SECONDARY_WAIT_SECONDS
                last_retry = time.time()
                while time.time() < deadline:
                    # 常驻监听器随时可能抓到 token，一有就立刻兑现成二维码
                    if not qr_state["pushed"] and qr_state["token"]:
                        _push_scan_qr(account_id, qr_state["token"], qr_state)
                    if not check_secondary_verification(
                        page, account_id, timeout=2, auto_action=False, qr_state=qr_state
                    ):
                        _emit(account_id, "[登录] 二次验证已完成")
                        break
                    # 二维码始终没拿到时定期重新尝试抓取，而不是干等到超时（issue #22）
                    if not qr_state["pushed"] and time.time() - last_retry >= QR_RETRY_INTERVAL:
                        last_retry = time.time()
                        _emit(account_id, "[二次验证] 仍未获得二维码，重试抓取", "warn")
                        check_secondary_verification(
                            page, account_id, timeout=3, auto_action=True, qr_state=qr_state
                        )
                    time.sleep(3)
                else:
                    _emit(account_id, "[登录] 二次验证等待超时", "warn")
                if not qr_state["pushed"]:
                    _emit(
                        account_id,
                        "[二次验证] 始终未能获取扫码二维码，请在浏览器弹窗中手动选择验证方式，"
                        "或把设置里的登录方式改为「扫码登录」",
                        "error",
                    )
                    _debug_shot(page, phone, "secondary_no_qr", account_id)
                    bus.status(account_id, "secondary", "二维码获取失败")
                    if method == "auto":
                        fallback_reason = "二次验证未能获取二维码"
                        use_qr = True
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"检查二次验证出错：{e}", "warn")

        def _confirm_session() -> tuple[bool, str, Optional[str], Optional[str]]:
            """等待服务端确认登录成功。旧 cookie 可能仍存在，不能只检查 cookie 名称。"""
            cookie_str, ok = "", False
            uid, nickname = None, None
            deadline = time.time() + CONFIRM_WAIT_SECONDS
            while time.time() < deadline:
                cookies = context.cookies("https://music.163.com")
                cookie_str = cookies_to_str(cookies)
                if has_login_cookie(cookies):
                    uid, nickname, _error = fetch_session_user(page, S.MUSICIAN_HOME_URL)
                    if uid:
                        ok = True
                        break
                time.sleep(1)
            return ok, cookie_str, uid, nickname

        def _try_qrcode_login() -> tuple[bool, str, Optional[str], Optional[str]]:
            bus.status(account_id, "secondary", "等待扫码登录")
            try:
                if login_with_qrcode(page, account_id, phone):
                    return _confirm_session()
            except NetworkRiskError:
                _debug_shot(page, phone, "network_risk_qrcode", account_id)
                _emit(account_id, "[扫码登录] 页面提示网络环境风险", "error")
            except Exception as exc:  # noqa: BLE001
                _emit(account_id, f"[扫码登录] 异常：{exc}", "warn")
            return False, cookies_to_str(context.cookies("https://music.163.com")), None, None

        if use_qr:
            if fallback_reason:
                _emit(account_id, f"[登录] 密码登录未成功（{fallback_reason}），自动切换扫码登录", "warn")
            ok, cookie_str, uid, nickname = _try_qrcode_login()
        else:
            ok, cookie_str, uid, nickname = _confirm_session()
            # auto 模式下密码登录最终没拿到有效会话，再给一次扫码机会
            if not ok and method == "auto":
                _emit(account_id, "[登录] 密码登录未通过服务端校验，自动切换扫码登录", "warn")
                ok, cookie_str, uid, nickname = _try_qrcode_login()

        if ok:
            _emit(account_id, f"已获取账号信息：uid={uid}，昵称={nickname or '-'}")
            _emit(account_id, "登录成功，服务端已确认 Cookie 有效")
            bus.status(account_id, "login_ok", "登录成功")
            return {"ok": True, "cookie_str": cookie_str, "uid": uid, "nickname": nickname, "message": "ok"}

        _emit(account_id, "登录未通过服务端会话校验（Cookie 缺失或已失效）", "error")
        _debug_shot(page, phone, "no_login_cookie", account_id)
        bus.status(account_id, "login_fail", "Cookie 未通过服务端校验")
        return {"ok": False, "cookie_str": cookie_str, "message": "server session invalid"}
