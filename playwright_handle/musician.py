"""
使用 Playwright 打开网易云音乐【音乐人后台】页面，并监听循环任务列表接口：
  /weapi/nmusician/workbench/mission/cycle/list

用途：
- 规避部分场景下直接请求 weapi 接口需要 checkToken 导致的 301/风控问题
- 通过网页端同源请求拿到接口返回 JSON
"""

from __future__ import annotations

import os
from typing import Any

from playwright.sync_api import sync_playwright

from core import logger

MUSICIAN_HOME_URL = "https://music.163.com/musician/artist/home"


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


