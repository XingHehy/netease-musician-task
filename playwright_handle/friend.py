"""
使用 Playwright 在网页版网易云音乐的「动态/朋友」页发一条笔记，并给笔记配上音乐。
依赖 `playwright_handle/login.py` 已经登录并写入浏览器持久化 profile。
"""

from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright, Page, Frame

from core import logger, NeteaseClient, TaskManager

FRIEND_URL = "https://music.163.com/#/friend"
PROFILE_DIR = ".playwright_profile_netease"  # 作为独立脚本运行时使用；集成到 main.py 时会传参覆盖
VIP_RIGHT_URL = "https://y.music.163.com/g/yida/7d4d0e9f89884a68b8eddea50b5aa6a6"


def _scopes(page: Page | Frame):
    yield page
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        yield fr


def _first_with_selector(page: Page, selector: str) -> Frame | Page:
    """在所有 frame 中找到第一个包含指定 selector 的 scope。"""
    for scope in _scopes(page):
        try:
            if scope.locator(selector).count() > 0:
                return scope
        except Exception:
            continue
    return page


def _cookies_to_cookie_str(cookies: list[dict]) -> str:
    """将 Playwright cookies 转为 requests/NeteaseClient 使用的 cookie_str。"""
    pairs = []
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


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


def _log_vip_task_progress(page: Page) -> None:
    """
    进入音乐人权益页，监听 VIP 任务进度接口：
    https://interface.music.163.com/weapi/nmusician/workbench/special/right/vip/info

    从返回中找到名称为「即日起30天内发布图文笔记天数≥4」的任务，
    并在日志中打印 totalCompleteNum / progressRate。
    """
    target_task_name = "即日起30天内发布图文笔记天数≥4"

    def _is_target(resp) -> bool:
        try:
            return (
                "nmusician/workbench/special/right/vip/info" in resp.url
                and "interface.music.163.com" in resp.url
                and resp.request.method == "POST"
            )
        except Exception:
            return False

    logger.info("打开音乐人权益页，监听 VIP 任务进度接口...")
    try:
        with page.expect_response(_is_target, timeout=30000) as resp_info:
            # 打开 y.music 的活动页，页面内部会自动请求目标接口
            page.goto(VIP_RIGHT_URL, wait_until="domcontentloaded")
        resp = resp_info.value
    except Exception as e:
        logger.warning(f"未能捕获 VIP 任务进度接口响应：{e}")
        return

    try:
        data = resp.json()
    except Exception as e:
        try:
            raw_text = resp.text()
        except Exception:
            raw_text = ""
        logger.warning(f"解析 VIP 任务进度接口 JSON 失败：{e}，原始内容片段：{raw_text[:300]}")
        return

    logger.info(f"VIP 任务接口返回：{str(data)[:300]}")

    try:
        further = (data or {}).get("data", {}).get("furtherTask", {})
        children = further.get("children") or []
        if not isinstance(children, list) or not children:
            logger.warning("VIP 任务返回中 furtherTask.children 为空或不是列表")
            return

        found = False
        for child in children:
            if not isinstance(child, dict):
                continue
            # 文案字段可能叫 description / name / title，尽量多兜一下
            desc = child.get("description") or child.get("name") or child.get("title") or ""
            if desc == target_task_name:
                total = child.get("totalCompleteNum")
                progress = child.get("progressRate")
                logger.info(
                    f"发现任务「{target_task_name}」：totalCompleteNum={total}，progressRate={progress}"
                )
                found = True
                break

        if not found:
            logger.warning(
                f"在 furtherTask.children 中未找到名称为「{target_task_name}」的任务，"
                f"children 数量={len(children)}"
            )
    except Exception as e:
        logger.warning(f"解析 VIP 任务进度数据时出错：{e}")

def share_note_and_delete(
    profile_dir: str,
    msg: str,
    search_keyword: str = "你好",
    cookie_str: str | None = None,
    phone: str | None = None,
    password: str | None = None,
) -> bool:
    """
    供 main.py 调用：用浏览器发布笔记（配音乐）并监听分享接口返回，拿到 event_id 后等待删除。
    """
    os.makedirs("log", exist_ok=True)

    def _run_once(_cookie_str: str | None) -> bool:
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

            logger.info("打开朋友/动态页，用于发布笔记...")
            page.goto(FRIEND_URL, wait_until="networkidle")

            # 1. 找到包含发笔记按钮的 frame（若没找到通常表示未登录）
            scope = _first_with_selector(page, "#pubEvent")
            if scope.locator("#pubEvent").count() == 0:
                logger.warning("未找到发笔记按钮，疑似未登录态")
                context.close()
                return False

            # 2. 点击「发笔记」按钮
            scope.click("#pubEvent")
            logger.info("已点击发笔记按钮")

            # 3. 输入内容
            textarea = scope.locator("textarea.u-txt.area.j-flag[placeholder='一起聊聊吧~']").first
            textarea.wait_for(state="visible", timeout=15000)
            textarea.fill(msg)
            logger.info("已输入笔记内容")

            # 4. 点击「给笔记配上音乐」
            scope.get_by_text("给笔记配上音乐", exact=True).click()
            logger.info("已点击给笔记配上音乐")

            # 5. 搜索并选择第一首
            search_scope = _first_with_selector(page, ".m-lysearch")
            search_input = search_scope.locator(".m-lysearch input.u-txt.txt.j-flag").first
            search_input.wait_for(state="visible", timeout=15000)
            search_input.fill(search_keyword)
            search_input.press("Enter")
            logger.info(f"已在搜索框输入“{search_keyword}”并回车")

            # 你贴的 DOM 里结果是：.srchlist ... <li class="sitm ...">
            first_item = search_scope.locator(".srchlist li.sitm").first
            # 先等元素挂载出来，再等可见
            first_item.wait_for(state="attached", timeout=30000)
            first_item.wait_for(state="visible", timeout=30000)
            first_item.click()
            logger.info("已选择搜索结果中的第一条歌曲（li.sitm）")

            # 6. 点击「分享」按钮
            share_btn = scope.locator("a.u-btn2.u-btn2-2.u-btn2-w2.j-flag[data-action='share']").first
            share_btn.wait_for(state="visible", timeout=15000)

            # 7. 监听分享接口返回（必须在点击前开始监听，避免竞态错过）
            page_obj = scope.page if isinstance(scope, Frame) else scope
            with page_obj.expect_response(
                lambda r: "weapi/share/friends/resource" in r.url and r.request.method == "POST",
                timeout=20000,
            ) as resp_info:
                share_btn.click()
            logger.info("已点击分享按钮，已捕获接口返回")

            resp = resp_info.value
            try:
                data = resp.json()
            except Exception:
                data = {}

            logger.info(f"分享接口返回：{str(data)[:200]}")
            event_id = data.get("event", {}).get("id")
            if not event_id:
                logger.warning("分享接口返回中未获取到 event.id，发布可能失败/触发验证")
                context.close()
                return False

            # 8. 发布成功后，进入音乐人权益页，监听并打印 VIP 任务进度
            try:
                _log_vip_task_progress(page)
            except Exception as e:
                logger.warning(f"获取 VIP 任务进度时发生异常：{e}")

            # 9. 删除动态（复用核心 TaskManager）
            logger.info(f"分享成功，event_id={event_id}，等待 10 秒后删除动态...")
            time.sleep(10)
            cookies = context.cookies("https://music.163.com")
            cookie_str2 = _cookies_to_cookie_str(cookies)
            client = NeteaseClient(cookie_str=cookie_str2)
            task = TaskManager(client)
            delete_res = task.delete_dynamic(event_id)
            logger.info(f"删除动态结果: {delete_res}")

            context.close()
            return True

    # 第一次尝试：用传入 cookie 注入
    if _run_once(cookie_str):
        return True

    # 若仍未登录且给了账号密码，则执行登录刷新 profile，再重试一次（不再依赖旧 cookie）
    if phone and password:
        logger.info("Cookie 注入后仍未登录，开始执行 Playwright 登录流程刷新浏览器态...")
        from playwright_handle.login import browser_login

        try:
            new_cookie_str = browser_login(phone, password, profile_dir=profile_dir)
        except Exception as e:
            logger.error(f"Playwright 登录失败，无法继续发布：{e}")
            return False
        return _run_once(new_cookie_str)

    return False


def main():
    msg = f"{time.strftime('%Y年%m月%d日%H:%M:%S')}早上好"
    ok = share_note_and_delete(PROFILE_DIR, msg, search_keyword="你好")
    if not ok:
        raise SystemExit("发布失败：请确认当前 profile 已登录，或页面触发了额外验证。")


if __name__ == "__main__":
    main()


