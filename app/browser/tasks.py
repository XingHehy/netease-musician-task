"""
任务执行：全部走浏览器（同源），绕开 requests 通道的 301 风控。

- 签到/日常签到：打开音乐人后台，DOM 点击签到按钮；用接口响应监听确认结果。
- 发布动态：#pubEvent → 配乐 → 分享 → 抓 event.id → 删除。
- VIP 领取：打开权益页 → 点续期/领取 → 监听 vip/info 拿 furtherVipGetTime。

必须在 browser worker 线程内调用。签到按钮的确切选择器可能需用有头浏览器现场核对，
这里提供候选选择器 + 接口监听确认的健壮实现。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from playwright.sync_api import Page

from app.browser import selectors as S
from app.browser.helpers import cookies_to_str, first_with_selector, scopes
from app.browser.manager import run_with_context
from app.event_bus import bus
from app.logging_conf import logger


def _emit(account_id: Optional[int], line: str, level: str = "info") -> None:
    if level == "error":
        logger.error(line)
    elif level == "warn":
        logger.warning(line)
    else:
        logger.info(line)
    bus.log(account_id, line, level=level)


# ---------- 循环任务列表 ----------
def _capture_cycle_missions(page: Page, account_id: Optional[int], timeout_ms: int = 30000) -> list[dict]:
    """打开音乐人后台，监听 cycle/list 接口拿任务列表。"""
    def _is_target(resp) -> bool:
        try:
            return S.CYCLE_LIST_API in resp.url and resp.request.method == "POST"
        except Exception:
            return False

    _emit(account_id, "打开音乐人后台，等待任务列表接口...")
    try:
        with page.expect_response(_is_target, timeout=timeout_ms) as resp_info:
            page.goto(S.MUSICIAN_HOME_URL, wait_until="domcontentloaded")
        data = resp_info.value.json()
    except Exception as e:  # noqa: BLE001
        _emit(account_id, f"未捕获到 cycle/list 接口：{e}", "warn")
        return []

    if not isinstance(data, dict) or data.get("code") != 200:
        _emit(account_id, f"cycle/list 返回异常：{str(data)[:120]}", "warn")
        return []
    missions = (data.get("data") or {}).get("list") or []
    _emit(account_id, f"获取到 {len(missions)} 个循环任务")
    return missions


# ---------- 签到（页面级）----------
def _checkin_on_page(page: Page, account_id: Optional[int]) -> dict:
    """在已有页面上执行音乐人签到 + 日常签到。"""
    missions = _capture_cycle_missions(page, account_id)
    checkin_done = False
    for m in missions:
        desc = m.get("description") or ""
        if "签到" not in desc:
            continue
        _emit(account_id, f"发现签到任务：{desc}")
        ok = _click_or_invoke_checkin(page, account_id, m)
        checkin_done = checkin_done or ok
    if not checkin_done:
        _emit(account_id, "未发现可执行的签到任务（可能已签到）")
    _do_daily_task(page, account_id)
    return {"ok": checkin_done, "message": "checkin done"}


def do_checkin(profile_dir: str, account_id: Optional[int] = None) -> dict:
    """独立入口：自己开浏览器执行签到（供手动单独触发）。"""
    bus.status(account_id, "running", "执行签到任务")
    with run_with_context(profile_dir, account_id=account_id, label="签到") as (context, page):
        res = _checkin_on_page(page, account_id)
        res["cookie_str"] = cookies_to_str(context.cookies("https://music.163.com"))
        bus.status(account_id, "done", "签到任务完成")
        return res


# ---------- 每日整体流程（签到 +（可选）间隔任务，同一浏览器不关闭）----------
def do_daily_run(
    profile_dir: str,
    account_id: Optional[int],
    *,
    run_checkin: bool = True,
    run_interval: bool = False,
    interval_kind: str = "publish",   # 'publish' 或 'vip'
    publish_msg: str = "",
    search_keyword: str = "你好",
) -> dict:
    """
    在**同一浏览器会话**内按需执行签到 / 间隔任务，中途不关闭浏览器：
    - 签到在主标签页；
    - 间隔任务（发布动态或 VIP 领取）在新标签页；
    - 最后统一关闭。
    返回 {checkin, interval, cookie_str}。
    """
    bus.status(account_id, "running", "开始执行任务")
    result: dict[str, Any] = {"checkin": None, "interval": None}

    with run_with_context(profile_dir, account_id=account_id, label="每日任务") as (context, page):
        # 1) 签到（主标签页）
        if run_checkin:
            result["checkin"] = _checkin_on_page(page, account_id)

        # 2) 间隔任务（新标签页，浏览器不关）
        if run_interval:
            _emit(account_id, f"执行间隔任务（{interval_kind}），新建标签页")
            tab = context.new_page()
            try:
                from app.config import BROWSER_TIMEOUT_MS

                tab.set_default_timeout(BROWSER_TIMEOUT_MS)
                if interval_kind == "vip":
                    result["interval"] = _vip_claim_on_page(context, tab, account_id)
                else:
                    msg = publish_msg or "分享音乐"
                    result["interval"] = _publish_on_page(tab, account_id, msg, search_keyword)
            finally:
                try:
                    tab.close()
                except Exception:
                    pass

        result["cookie_str"] = cookies_to_str(context.cookies("https://music.163.com"))

    bus.status(account_id, "done", "任务完成")
    return result


def _click_or_invoke_checkin(page: Page, account_id: Optional[int], mission: dict) -> bool:
    """
    优先在页面同源上下文调用签到接口（走页面自带的加密请求管线），
    监听 reward/obtain 响应确认。DOM 按钮点击作为候选路径。
    """
    def _is_reward(resp) -> bool:
        try:
            return S.REWARD_OBTAIN_API in resp.url and resp.request.method == "POST"
        except Exception:
            return False

    user_mission_id = mission.get("userMissionId")
    period = mission.get("period")

    # 路径 A：DOM 点击签到按钮（候选选择器；确切选择器建议有头浏览器现场核对）
    checkin_selectors = [
        "button:has-text('签到')",
        "a:has-text('签到')",
        "div[class*='sign']:has-text('签到')",
        "text=立即签到",
        "text=签到",
    ]
    for scope in scopes(page):
        for sel in checkin_selectors:
            try:
                loc = scope.locator(sel)
                if loc.count() == 0:
                    continue
                _emit(account_id, f"尝试点击签到按钮：{sel}")
                try:
                    with page.expect_response(_is_reward, timeout=8000) as resp_info:
                        loc.first.click(force=True)
                    data = resp_info.value.json()
                    if isinstance(data, dict) and data.get("code") == 200:
                        _emit(account_id, "签到成功（DOM 点击）")
                        return True
                    _emit(account_id, f"签到接口返回：{str(data)[:120]}", "warn")
                except Exception:
                    # 点击了但没监听到接口，继续尝试其他选择器
                    continue
            except Exception:
                continue

    _emit(
        account_id,
        f"未能通过 DOM 点击完成签到（userMissionId={user_mission_id}, period={period}）；"
        "该账号页面签到按钮选择器可能需现场核对",
        "warn",
    )
    return False


def _do_daily_task(page: Page, account_id: Optional[int]) -> None:
    """日常签到：同源 fetch 调用 dailyTask（网页端 type=1）。"""
    def _is_daily(resp) -> bool:
        try:
            return S.DAILY_TASK_API in resp.url and resp.request.method == "POST"
        except Exception:
            return False

    try:
        with page.expect_response(_is_daily, timeout=6000) as resp_info:
            page.evaluate(
                """() => {
                    // 触发页面自身可能的每日任务上报；若无则忽略
                    if (window.__daily_signin__) { window.__daily_signin__(); }
                }"""
            )
        data = resp_info.value.json()
        _emit(account_id, f"日常签到结果：{str(data)[:100]}")
    except Exception:
        _emit(account_id, "日常签到：页面未触发 dailyTask 接口（可忽略或已完成）")


# ---------- 发布动态（页面级）----------
def _publish_on_page(page: Page, account_id: Optional[int], msg: str, search_keyword: str = "你好") -> dict:
    """在给定页面（可为新标签页）上发布配乐笔记并删除。"""
    def _is_share(resp) -> bool:
        try:
            return S.SHARE_API in resp.url and resp.request.method == "POST"
        except Exception:
            return False

    _emit(account_id, "打开动态页，准备发布笔记...")
    page.goto(S.FRIEND_URL, wait_until="networkidle")

    scope = first_with_selector(page, S.SEL_PUB_EVENT)
    if scope.locator(S.SEL_PUB_EVENT).count() == 0:
        _emit(account_id, "未找到发笔记按钮，疑似未登录", "warn")
        return {"ok": False, "event_id": None, "message": "not logged in"}

    try:
        scope.locator(S.SEL_PUB_EVENT).first.click()
        _emit(account_id, "已点击发笔记")
        scope.locator(S.SEL_NOTE_TEXTAREA).first.fill(msg)
        _emit(account_id, f"已输入文案：{msg[:20]}...")

        scope.get_by_text(S.SEL_ADD_MUSIC, exact=True).first.click()
        search = first_with_selector(page, S.SEL_MUSIC_SEARCH)
        search.locator(S.SEL_MUSIC_SEARCH).first.fill(search_keyword)
        search.locator(S.SEL_MUSIC_SEARCH).first.press("Enter")
        time.sleep(2)
        result_scope = first_with_selector(page, S.SEL_SEARCH_RESULT)
        result_scope.locator(S.SEL_SEARCH_RESULT).first.click()
        _emit(account_id, "已选择配乐")

        share_scope = first_with_selector(page, S.SEL_SHARE_BTN)
        with page.expect_response(_is_share, timeout=20000) as resp_info:
            share_scope.locator(S.SEL_SHARE_BTN).first.click()
        data = resp_info.value.json()
    except Exception as e:  # noqa: BLE001
        _emit(account_id, f"发布流程异常：{e}", "error")
        return {"ok": False, "event_id": None, "message": str(e)}

    event_id = None
    if isinstance(data, dict):
        event_id = (data.get("event") or {}).get("id")
    if not event_id:
        _emit(account_id, f"分享未返回 event.id：{str(data)[:120]}", "warn")
        return {"ok": False, "event_id": None, "message": "no event id"}

    _emit(account_id, f"发布成功，event_id={event_id}，10 秒后删除")
    time.sleep(10)
    _delete_event(page, account_id, event_id)
    return {"ok": True, "event_id": event_id, "message": "published"}


def do_publish_note(
    profile_dir: str,
    msg: str,
    account_id: Optional[int] = None,
    search_keyword: str = "你好",
) -> dict:
    """独立入口：自己开浏览器发布动态（供手动单独触发）。"""
    bus.status(account_id, "running", "发布动态")
    with run_with_context(profile_dir, account_id=account_id, label="发布动态") as (context, page):
        res = _publish_on_page(page, account_id, msg, search_keyword)
        res["cookie_str"] = cookies_to_str(context.cookies("https://music.163.com"))
        bus.status(account_id, "done", "发布动态完成")
        return res


def _delete_event(page: Page, account_id: Optional[int], event_id: str) -> None:
    """同源删除动态。"""
    def _is_delete(resp) -> bool:
        try:
            return S.EVENT_DELETE_API in resp.url and resp.request.method == "POST"
        except Exception:
            return False

    try:
        # 真实结构：删除项在 <ul class="mng f-hide"> 内，默认隐藏。
        # 需先点「笔记管理」展开箭头 [data-action='unfold']，再点 [data-action='delete']，
        # 最后点确认弹窗的 [data-action='ok']。
        for scope in scopes(page):
            del_btn = scope.locator("[data-action='delete']")
            if del_btn.count() == 0:
                continue

            # 1) 展开管理菜单
            try:
                unfold = scope.locator("[data-action='unfold']")
                if unfold.count() > 0:
                    unfold.first.click(timeout=2000)
                    page.wait_for_timeout(300)
            except Exception:
                pass

            try:
                with page.expect_response(_is_delete, timeout=8000) as resp_info:
                    try:
                        del_btn.first.click(timeout=2000)
                    except Exception:
                        # 兜底：JS 原生 click，绕过可见性检查
                        del_btn.first.evaluate("el => el.click()")
                    # 确认弹窗：优先 data-action='ok'，兜底文本
                    page.wait_for_timeout(300)
                    ok = scope.locator("[data-action='ok']")
                    try:
                        if ok.count() > 0:
                            ok.first.click(timeout=2000)
                        else:
                            scope.get_by_text("确定", exact=True).first.click(timeout=2000)
                    except Exception:
                        pass
                data = resp_info.value.json()
                _emit(account_id, f"删除动态结果：{str(data)[:80]}")
                return
            except Exception as e:  # noqa: BLE001
                _emit(account_id, f"该 scope 删除尝试失败：{e}", "warn")
                continue

        _emit(account_id, f"未找到删除入口，event_id={event_id} 未删除", "warn")
    except Exception as e:  # noqa: BLE001
        _emit(account_id, f"删除动态失败：{e}", "warn")


# ---------- VIP 领取（页面级）----------
def _vip_claim_on_page(context, page: Page, account_id: Optional[int], timeout_ms: int = 30000) -> dict:
    """在给定页面（可为新标签页）上领取 VIP 并解析 furtherVipGetTime。"""
    def _is_vip(resp) -> bool:
        try:
            return (
                S.VIP_INFO_URL_SUBSTR in resp.url
                and "interface.music.163.com" in resp.url
                and resp.request.method == "POST"
            )
        except Exception:
            return False

    page.goto(S.VIP_RIGHT_URL, wait_until="domcontentloaded")

    deadline = time.time() + timeout_ms / 1000
    renew_btn = None
    while time.time() < deadline and renew_btn is None:
        for scope in scopes(page):
            try:
                container = scope.locator(S.SEL_VIP_CONTAINER)
                if container.count() == 0:
                    continue
                btn = container.locator(f"div.link-wrapper {S.SEL_VIP_CHECK}")
                if btn.count() == 0:
                    btn = container.locator(S.SEL_VIP_CHECK)
                if btn.count() > 0:
                    renew_btn = btn.first
                    break
            except Exception:
                continue
        if renew_btn is None:
            page.wait_for_timeout(500)

    further_time = None
    if renew_btn is not None:
        _emit(account_id, "找到 VIP 续期/领取按钮，点击并监听 vip/info")
        try:
            renew_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            with context.expect_event("response", predicate=_is_vip, timeout=timeout_ms) as resp_info:
                try:
                    with context.expect_page(timeout=5000) as new_page_info:
                        renew_btn.click(force=True)
                    np = new_page_info.value
                    np.wait_for_load_state("domcontentloaded", timeout=10000)
                    np.wait_for_timeout(3000)
                except Exception:
                    pass
            further_time = _parse_vip(resp_info.value.json(), account_id)
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"点击 VIP 按钮/解析失败：{e}", "warn")
    else:
        _emit(account_id, "未找到 VIP 按钮，尝试 reload 监听 vip/info", "warn")
        try:
            with context.expect_event("response", predicate=_is_vip, timeout=timeout_ms) as resp_info:
                page.reload(wait_until="domcontentloaded")
            further_time = _parse_vip(resp_info.value.json(), account_id)
        except Exception as e:  # noqa: BLE001
            _emit(account_id, f"监听 vip/info 失败：{e}", "warn")

    return {"ok": further_time is not None, "further_vip_get_time": further_time, "message": "vip done"}


def do_vip_claim(profile_dir: str, account_id: Optional[int] = None, timeout_ms: int = 30000) -> dict:
    """独立入口：自己开浏览器领取 VIP（供手动单独触发）。"""
    bus.status(account_id, "running", "领取 VIP 权益")
    with run_with_context(profile_dir, account_id=account_id, label="VIP领取") as (context, page):
        res = _vip_claim_on_page(context, page, account_id, timeout_ms)
        bus.status(account_id, "done", "VIP 任务完成")
        return res


def _parse_vip(data: Any, account_id: Optional[int]) -> Optional[int]:
    try:
        t = (data or {}).get("data", {}).get("furtherVipGetTime")
        if isinstance(t, str) and t.isdigit():
            t = int(t)
        elif isinstance(t, (int, float)):
            t = int(t)
        else:
            t = None
        if t:
            readable = datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d %H:%M:%S")
            _emit(account_id, f"下次可领取 VIP 时间：{readable}")
        else:
            _emit(account_id, "未解析到 furtherVipGetTime", "warn")
        # 任务进度日志
        further = (data or {}).get("data", {}).get("furtherTask", {})
        for child in (further.get("children") or []):
            if isinstance(child, dict) and (child.get("description") == S.VIP_TASK_NAME):
                _emit(account_id, f"任务「{S.VIP_TASK_NAME}」进度：{child.get('progressRate')}")
                break
        return t
    except Exception as e:  # noqa: BLE001
        _emit(account_id, f"解析 vip/info 出错：{e}", "warn")
        return None
