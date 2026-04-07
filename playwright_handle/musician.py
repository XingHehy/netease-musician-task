"""
使用 Playwright 打开网易云音乐【音乐人后台】页面，并监听循环任务列表接口：
  /weapi/nmusician/workbench/mission/cycle/list

用途：
- 规避部分场景下直接请求 weapi 接口需要 checkToken 导致的 301/风控问题
- 通过网页端同源请求拿到接口返回 JSON
"""

from __future__ import annotations

import os
import time
from typing import Any

from playwright.sync_api import Frame, Page, sync_playwright

from core import logger

MUSICIAN_HOME_URL = "https://music.163.com/musician/artist/home"

VIP_INFO_URL_SUBSTR = "nmusician/workbench/special/right/vip/info"
VIP_TASK_NAME = "即日起30天内发布图文笔记天数≥4"


def _scopes(page: Page | Frame):
    """
    兼容可能存在 iframe 的场景：遍历 page.frames，找到第一个包含 selector 的 scope。
    """
    yield page
    if isinstance(page, Page):
        for fr in page.frames:
            if fr is page.main_frame:
                continue
            yield fr


def _first_with_selector(page: Page, selector: str) -> Page | Frame:
    """在所有 frame 中找到第一个包含指定 selector 的 scope。"""
    for scope in _scopes(page):
        try:
            if scope.locator(selector).count() > 0:
                return scope
        except Exception:
            continue
    return page


def _parse_vip_info_payload(
    data: Any,
    *,
    vip_further_get_time_callback=None,
) -> int | None:
    """从 VIP info 接口返回中提取 furtherVipGetTime，并输出任务进度日志。"""

    further_vip_get_time = None
    try:
        further_vip_get_time = (data or {}).get("data", {}).get("furtherVipGetTime")
        if isinstance(further_vip_get_time, str) and further_vip_get_time.isdigit():
            further_vip_get_time = int(further_vip_get_time)
        elif isinstance(further_vip_get_time, (int, float)):
            further_vip_get_time = int(further_vip_get_time)
        else:
            further_vip_get_time = None

        if further_vip_get_time:
            logger.info(
                f"解析到 furtherVipGetTime={further_vip_get_time}（下次可领取 VIP 的时间，ms）"
            )
            if vip_further_get_time_callback:
                try:
                    vip_further_get_time_callback(further_vip_get_time)
                except Exception as e:
                    logger.warning(f"执行 vip_further_get_time_callback 失败：{e}")
        else:
            logger.warning("未从 VIP 接口返回中解析到 data.furtherVipGetTime")
    except Exception as e:
        logger.warning(f"解析 furtherVipGetTime 时出错：{e}")

    # 打印任务进度日志（可选，不影响返回值）
    try:
        further = (data or {}).get("data", {}).get("furtherTask", {})
        children = further.get("children") or []
        if not isinstance(children, list) or not children:
            logger.warning("VIP 任务返回中 furtherTask.children 为空或不是列表")
            return further_vip_get_time

        found = False
        for child in children:
            if not isinstance(child, dict):
                continue
            desc = child.get("description") or child.get("name") or child.get("title") or ""
            if desc == VIP_TASK_NAME:
                total = child.get("totalCompleteNum")
                progress = child.get("progressRate")
                logger.info(
                    f"发现任务「{VIP_TASK_NAME}」：totalCompleteNum={total}，progressRate={progress}"
                )
                found = True
                break

        if not found:
            logger.warning(
                f"在 furtherTask.children 中未找到名称为「{VIP_TASK_NAME}」的任务，"
                f"children 数量={len(children)}"
            )
    except Exception as e:
        logger.warning(f"解析 VIP 任务进度数据时出错：{e}")

    return further_vip_get_time


def open_vip_right_page_and_listen(
    profile_dir: str,
    *,
    cookie_str: str | None = None,
    phone: str | None = None,
    password: str | None = None,
    timeout_ms: int = 30000,
    vip_further_get_time_callback=None,
) -> int | None:
    """
    按你的要求：打开音乐人首页（music.163.com/musician/artist/home），
    若页面中存在 `vip-container` 区域的续期/领取按钮则点击，并监听 VIP info 接口，
    从返回里提取 furtherVipGetTime（ms）。
    """

    os.makedirs("log", exist_ok=True)

    def _run_once(_cookie_str: str | None) -> int | None:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            if _cookie_str:
                try:
                    pw_cookies = _cookie_str_to_playwright_cookies(_cookie_str)
                    if pw_cookies:
                        context.add_cookies(pw_cookies)
                        logger.info(f"已注入 Cookie 到浏览器（{len(pw_cookies)} 条）")
                except Exception as e:
                    logger.warning(f"注入 Cookie 失败：{e}")

            # 放在 goto 前：如果页面初始化也会请求 vip/info，后续仍能捕获点击后的那次
            page.set_default_timeout(timeout_ms)
            page.goto(MUSICIAN_HOME_URL, wait_until="domcontentloaded")

            def _is_target(resp) -> bool:
                try:
                    return (
                        VIP_INFO_URL_SUBSTR in resp.url
                        and "interface.music.163.com" in resp.url
                        and resp.request.method == "POST"
                    )
                except Exception:
                    return False

            # 页面可能需要更久渲染（SPA 异步加载），不要立刻 count。
            # 在 timeout_ms 内轮询找到 vip 区域内的续期/领取按钮后再点击。
            deadline = time.time() + timeout_ms / 1000
            renew_btn = None
            while time.time() < deadline and renew_btn is None:
                for scope in _scopes(page):
                    try:
                        vip_container = scope.locator("div.vip-container")
                        if vip_container.count() == 0:
                            continue

                        btn = vip_container.locator("div.link-wrapper span.check")
                        if btn.count() == 0:
                            btn = vip_container.locator("span.check")

                        if btn.count() > 0:
                            renew_btn = btn.first
                            break
                    except Exception:
                        continue

                if renew_btn is None:
                    page.wait_for_timeout(500)

            if renew_btn is not None:
                try:
                    renew_btn.wait_for(state="visible", timeout=timeout_ms // 2)
                except Exception:
                    pass

                logger.info("找到 VIP 续期/领取按钮，准备点击并监听 vip/info 接口...")
                try:
                    # 用 context 级别监听，避免点击导致原 page 被替换/关闭
                    with context.expect_event("response", predicate=_is_target, timeout=timeout_ms) as resp_info:
                        renew_btn.click()
                    resp = resp_info.value
                    data = resp.json()
                    return _parse_vip_info_payload(
                        data,
                        vip_further_get_time_callback=vip_further_get_time_callback,
                    )
                except Exception as e:
                    logger.warning(f"点击 VIP 按钮后捕获/解析 vip/info 接口失败：{e}")
                    return None

            logger.warning("未找到 VIP 按钮：仍尝试监听 vip/info 获取 furtherVipGetTime...")
            try:
                # 先启动监听，再 reload（避免竞态）
                with context.expect_event("response", predicate=_is_target, timeout=timeout_ms) as resp_info:
                    page.reload(wait_until="domcontentloaded")
                resp = resp_info.value
                data = resp.json()
                return _parse_vip_info_payload(
                    data,
                    vip_further_get_time_callback=vip_further_get_time_callback,
                )
            except Exception as e:
                logger.warning(f"监听 vip/info 接口失败：{e}")
                return None

            # context.close() 不需要手动写在 finally：sync_playwright 会在 with 退出时清理

    # 第一次尝试：用传入 cookie 注入（如果有）
    res = _run_once(cookie_str)
    if res is not None:
        return res

    # 若未成功且给了账号密码，则执行登录刷新 profile，再重试一次（不再依赖旧 cookie）
    if phone and password:
        logger.info("首次未成功解析 furtherVipGetTime，尝试 Playwright 登录刷新浏览器态后重试一次...")
        from playwright_handle.login import browser_login

        try:
            new_cookie_str = browser_login(phone, password, profile_dir=profile_dir)
        except Exception as e:
            logger.error(f"Playwright 登录失败：{e}")
            return None
        return _run_once(new_cookie_str)

    return res


def _cookie_str_to_playwright_cookies(cookie_str: str) -> list[dict]:
    """
    将 "k=v; k2=v2" 转成 Playwright 可 add_cookies 的结构。
    注：只用于 music.163.com 域下的简单 Cookie 注入。
    """
    cookies: list[dict] = []
    if not cookie_str:
        return cookies
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            continue
        cookies.append(
            {
                "name": k,
                "value": v,
                "domain": ".music.163.com",
                "path": "/",
            }
        )
    return cookies


def get_musician_cycle_mission_by_playwright(
    profile_dir: str,
    *,
    cookie_str: str | None = None,
    phone: str | None = None,
    password: str | None = None,
    actionType: str = "102",
    platform: str = "200",
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """
    打开 https://music.163.com/musician/artist/home 并监听
    /weapi/nmusician/workbench/mission/cycle/list 接口返回。

    返回：
    - 成功：接口响应 JSON（dict）
    - 失败：{"code": 250, "msg": "..."} 或 {"code": 301, "msg": "..."}
    """
    os.makedirs("log", exist_ok=True)

    def _run_once(_cookie_str: str | None) -> dict[str, Any]:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # 先注入 cookie（如果有），避免打开后是未登录态
            if _cookie_str:
                try:
                    pw_cookies = _cookie_str_to_playwright_cookies(_cookie_str)
                    if pw_cookies:
                        context.add_cookies(pw_cookies)
                        logger.info(f"已注入 Cookie 到浏览器（{len(pw_cookies)} 条）")
                except Exception as e:
                    logger.warning(f"注入 Cookie 失败：{e}")

            # 监听循环任务列表接口（需要在触发请求之前开始监听，避免竞态）
            def _is_target(resp) -> bool:
                try:
                    return (
                        "/weapi/nmusician/workbench/mission/cycle/list" in resp.url
                        and resp.request.method == "POST"
                    )
                except Exception:
                    return False

            logger.info("打开音乐人后台首页，并等待 cycle mission 接口返回...")
            try:
                with page.expect_response(_is_target, timeout=timeout_ms) as resp_info:
                    # domcontentloaded 更快，接口通常在页面初始化阶段就会请求
                    page.goto(MUSICIAN_HOME_URL, wait_until="domcontentloaded")
                resp = resp_info.value
            except Exception as e:
                context.close()
                return {"code": 250, "msg": f"未捕获到 cycle/list 接口响应（timeout={timeout_ms}ms）：{e}"}

            # 打印请求体（便于确认 actionType/platform）
            try:
                req = resp.request
                logger.info(f"捕获请求：{req.method} {req.url}")
            except Exception:
                pass

            try:
                data = resp.json()
            except Exception as e:
                try:
                    txt = resp.text()
                except Exception:
                    txt = ""
                context.close()
                return {"code": 250, "msg": f"解析接口 JSON 失败：{e}", "raw": txt[:500]}

            context.close()
            return data if isinstance(data, dict) else {"code": 250, "msg": "接口返回不是 JSON 对象", "data": data}

    # 第一次尝试：用传入 cookie 注入（如果有）
    res = _run_once(cookie_str)
    if isinstance(res, dict) and res.get("code") == 200:
        return res

    # 若仍未登录且给了账号密码，则执行登录刷新 profile，再重试一次（不再依赖旧 cookie）
    if phone and password:
        logger.info("首次未成功获取任务列表，尝试 Playwright 登录刷新浏览器态后重试一次...")
        from playwright_handle.login import browser_login

        try:
            new_cookie_str = browser_login(phone, password, profile_dir=profile_dir)
        except Exception as e:
            logger.error(f"Playwright 登录失败：{e}")
            return {"code": 301, "msg": f"playwright login failed: {e}"}
        return _run_once(new_cookie_str)

    return res


