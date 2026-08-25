#!/usr/bin/env python3
"""离线验证 v2.0.7 回移植的四个回归点（全部不需要真实账号/浏览器/DB）。

  ① _password_login_error 只拦截明确的账号/密码错误；
     验证挑战（8830 安全验证）、网络异常等非 200 响应必须放行给后续流程。
  ② run_daily_for_account 登录态失效时：自动登录成功 → 用刷新后的账号重试一次；
     自动登录失败 / 重试后仍失效 → 发通知并停止，不再无限循环。
  ③ settings 保存前的逐项校验：时间 / 非负整数 / 播放比例 / URL / 枚举值。
  ④ 调试截图接口的路径包含校验：不能越出账号目录，不能读非 png。

用法：
    python scripts/verify_auto_login_recovery.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.browser import login as L  # noqa: E402
from app import runner as R  # noqa: E402
from app.api import debug as D  # noqa: E402
from app.api.settings import _validate_setting  # noqa: E402


# ---------- ① 密码登录错误判定 ----------
class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> "_FakeLocator":
        return self

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return self._visible


class _FakeScope:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def get_by_text(self, text: str, exact: bool = False) -> _FakeLocator:
        return _FakeLocator(text in self._texts)


class _FakePage:
    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = texts or []

    def fake_scopes(self):
        return [_FakeScope(self._texts)]


def check_password_error_classifier() -> list[tuple[str, bool]]:
    original_scopes = L.scopes
    L.scopes = lambda page: page.fake_scopes()
    try:
        challenge_cases = [
            ({"code": 8830, "message": "需要安全验证"}, "8830 安全验证挑战"),
            ({"code": 8830, "message": ""}, "8830 无文案"),
            ({"code": -1, "message": "网络异常，请稍后再试"}, "网络异常"),
            ({"code": 460, "message": "请扫描二维码验证"}, "风控验证"),
        ]
        challenge_ok = True
        for data, desc in challenge_cases:
            page = _FakePage()
            result = L._password_login_error(page, {"data": data})
            challenge_ok = challenge_ok and result is None

        credential_cases = [
            ({"code": 400, "message": "账号或密码错误"}, "接口返回密码错误"),
            ({"code": 520, "message": "手机号或密码错误，请重试"}, "接口返回手机号或密码错误"),
        ]
        credential_ok = True
        for data, _desc in credential_cases:
            result = L._password_login_error(_FakePage(), {"data": data})
            credential_ok = credential_ok and bool(result) and "密码错误" in result

        page_text = L._password_login_error(
            _FakePage(["密码错误"]), {"data": {"code": 200}}
        )
        clean = L._password_login_error(_FakePage(), {"data": {"code": 200, "message": "ok"}})

        print(f"  验证挑战放行 {len(challenge_cases)} 例；密码错误拦截 {len(credential_cases)} 例")
        print(f"  页面文案判定：{page_text!r}；正常响应：{clean!r}")

        return [
            ("验证挑战/网络异常不再被误判为密码失败", challenge_ok),
            ("明确的账号/密码错误仍被拦截", credential_ok),
            ("页面可见的错误文案仍被识别", page_text == "密码错误"),
            ("正常 200 响应无错误", clean is None),
        ]
    finally:
        L.scopes = original_scopes


# ---------- ② 每日任务登录态自动恢复 ----------
class _FakeRepo:
    def __init__(self, account: dict) -> None:
        self.account = account
        self.logs: list[tuple] = []
        self.updates: list[dict] = []

    def get_account(self, account_id: int) -> dict:
        return dict(self.account)

    def add_log(self, account_id, task_type, status, message) -> None:
        self.logs.append((task_type, status, message))

    def update_account(self, account_id: int, **fields) -> None:
        self.updates.append(fields)

    @staticmethod
    def get_setting(key: str, default=None):
        return default

    @staticmethod
    def get_setting_int(key: str, default: int) -> int:
        return default


_DAILY_OK = {
    "ok": True,
    "auth_valid": True,
    "checkin": {
        "musician_checkin": {"ok": True, "message": "签到成功"},
        "daily_checkin": {"ok": True, "message": "已签到"},
    },
    "interval": None,
}


def _run_recovery_scenario(
    daily_results: list[dict],
    login_result: dict,
) -> dict:
    """在桩环境下跑一次 run_daily_for_account，返回执行轨迹。"""
    from datetime import datetime

    account = {
        "id": 1,
        "phone": "13800000000",
        "enabled": 1,
        "account_role": "musician",
        "profile_dir": "old_profile",
        "monthly_sends": 0,
        "month_tag": None,
        "further_vip_get_time": None,
        "last_send_date": datetime.now().strftime("%Y-%m-%d"),  # 未到发布间隔 → run=False
    }
    fake_repo = _FakeRepo(account)
    trace = {"daily_calls": [], "login_calls": 0, "notifications": []}

    def fake_run_blocking(fn, *args, **kwargs):
        trace["daily_calls"].append(args[0] if args else kwargs.get("profile_dir"))
        return daily_results[min(len(trace["daily_calls"]) - 1, len(daily_results) - 1)]

    def fake_run_login(account_id):
        trace["login_calls"] += 1
        fake_repo.account["profile_dir"] = "refreshed_profile"
        return login_result

    def fake_notify(content, title="", event="", extra=None):
        trace["notifications"].append({"title": title, "event": event, "content": content})

    saved = (R.repo, R._run_blocking, R.run_login, R.send_configured_notification)
    try:
        R.repo = fake_repo
        R._run_blocking = fake_run_blocking
        R.run_login = fake_run_login
        R.send_configured_notification = fake_notify
        R.run_daily_for_account(1)
    finally:
        R.repo, R._run_blocking, R.run_login, R.send_configured_notification = saved
    trace["logs"] = fake_repo.logs
    trace["updates"] = fake_repo.updates
    return trace


def check_daily_auto_recovery() -> list[tuple[str, bool]]:
    # 场景 A：登录态失效 → 自动登录成功 → 用刷新后的账号重试一次成功
    recovered = _run_recovery_scenario(
        [{"ok": False, "auth_valid": False}, _DAILY_OK],
        {"ok": True},
    )
    a_retried = len(recovered["daily_calls"]) == 2
    a_refreshed = a_retried and recovered["daily_calls"][1] == "refreshed_profile"
    a_login_logged = any(t == "login" and "自动重新登录" in m for t, _, m in recovered["logs"])
    a_daily_notified = any(n["event"] == "daily_result" for n in recovered["notifications"])

    # 场景 B：自动登录失败 → 通知并停止，不重试
    login_failed = _run_recovery_scenario(
        [{"ok": False, "auth_valid": False}],
        {"ok": False, "message": "server session invalid"},
    )
    b_no_retry = len(login_failed["daily_calls"]) == 1
    b_notified = any(
        n["event"] == "auto_login_failed" for n in login_failed["notifications"]
    )

    # 场景 C：自动登录成功但重试后仍失效 → 只登录一次，通知后停止
    still_bad = _run_recovery_scenario(
        [{"ok": False, "auth_valid": False}, {"ok": False, "auth_valid": False}],
        {"ok": True},
    )
    c_one_login = still_bad["login_calls"] == 1
    c_two_daily = len(still_bad["daily_calls"]) == 2
    c_notified = any(
        n["event"] == "auto_login_failed" for n in still_bad["notifications"]
    )

    print(f"  A 恢复成功：daily×{len(recovered['daily_calls'])}，"
          f"profile={recovered['daily_calls']}")
    print(f"  B 登录失败：daily×{len(login_failed['daily_calls'])}，"
          f"通知 {len(login_failed['notifications'])} 条")
    print(f"  C 仍失效：login×{still_bad['login_calls']}，"
          f"daily×{len(still_bad['daily_calls'])}，通知 {len(still_bad['notifications'])} 条")

    return [
        ("登录态失效后自动登录并重试", a_retried),
        ("重试使用刷新后的账号数据", a_refreshed),
        ("自动登录动作写入 login 日志", a_login_logged),
        ("恢复成功后照常发送每日结果通知", a_daily_notified),
        ("自动登录失败时不重试任务", b_no_retry),
        ("自动登录失败时发送 auto_login_failed 通知", b_notified),
        ("重试仍失效不会循环登录", c_one_login and c_two_daily),
        ("重试仍失效时发送通知并停止", c_notified),
    ]


# ---------- ③ 设置校验 ----------
def check_settings_validation() -> list[tuple[str, bool]]:
    valid_cases = [
        ("default_send_time", "09:30"),
        ("listen_start_time", "23:59"),
        ("local_listen_start_time", "00:00"),
        ("execution_interval_days", "3"),
        ("listen_daily_max", "0"),
        ("local_listen_monthly_max", "650"),
        ("local_listen_play_percent", "34"),
        ("local_listen_play_percent", "100"),
        ("listen_api_url", "https://listen.example.com/api"),
        ("listen_api_url", ""),  # 空 = 未配置，放行
        ("custom_webhook_url", "http://127.0.0.1:9000/hook"),
        ("headless", "0"),
        ("login_method", "qrcode"),
        ("notification_method", "pushplus"),
        ("custom_webhook_method", "GET"),
    ]
    invalid_cases = [
        ("default_send_time", "24:00"),
        ("execution_interval_days", "-1"),
        ("max_monthly_sends", "abc"),
        ("local_listen_play_percent", "10"),  # 低于前端 min=34
        ("local_listen_play_percent", "101"),
        ("headless", "false"),
        ("login_method", "qr"),
        ("notification_method", "sms"),
        ("custom_webhook_method", "PUT"),
    ]
    valid_ok = True
    for key, value in valid_cases:
        try:
            _validate_setting(key, value)
        except HTTPException:
            valid_ok = False
            print(f"  [意外拒绝] {key}={value!r}")

    rejected = 0
    for key, value in invalid_cases:
        try:
            _validate_setting(key, value)
            print(f"  [意外放行] {key}={value!r}")
        except HTTPException as exc:
            rejected += exc.status_code == 422 and 1 or 0

    print(f"  合法值 {len(valid_cases)} 例全部放行；非法值 {len(invalid_cases)} 例"
          f"拒绝 {rejected} 例")
    return [
        ("合法配置全部放行", valid_ok),
        ("非法配置全部以 422 拒绝", rejected == len(invalid_cases)),
    ]


# ---------- ④ 截图接口路径安全 ----------
def check_debug_screenshot_safety() -> list[tuple[str, bool]]:
    from fastapi.responses import FileResponse

    with tempfile.TemporaryDirectory() as tmp:
        phone_dir = os.path.join(tmp, "13800000000")
        os.makedirs(phone_dir, exist_ok=True)
        shot = os.path.join(phone_dir, "20260822_120000_shot.png")
        with open(shot, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        saved_repo, saved_dir = D.repo, D.DEBUG_DIR

        class _ShotRepo:
            @staticmethod
            def get_account(account_id: int) -> dict | None:
                return {"id": account_id, "phone": "13800000000"} if account_id == 1 else None

        try:
            D.repo = _ShotRepo
            D.DEBUG_DIR = tmp
            ok_file = D.debug_screenshot(1, "20260822_120000_shot.png")
            served = isinstance(ok_file, FileResponse)

            def _raises(account_id: int, filename: str) -> int | None:
                try:
                    D.debug_screenshot(account_id, filename)
                    return None
                except HTTPException as exc:
                    return exc.status_code

            missing = _raises(1, "20260101_000000_none.png")
            traversal = _raises(1, ".._.._evil.png")
            subpath = _raises(1, "sub/evil.png")
            not_png = _raises(1, "shot.jpg")
            no_account = _raises(99, "20260822_120000_shot.png")
        finally:
            D.repo, D.DEBUG_DIR = saved_repo, saved_dir

    print(f"  正常截图：FileResponse={served}")
    print(f"  不存在={missing} 越权名={traversal} 子路径={subpath} "
          f"非png={not_png} 无此账号={no_account}")

    return [
        ("存在的截图正常返回", served),
        ("不存在的截图返回 404", missing == 404),
        ("路径穿越文件名被拒绝（4xx）", traversal in (400, 404)),
        ("子路径文件名被拒绝", subpath == 400),
        ("非 png 后缀被拒绝", not_png == 400),
        ("不存在的账号返回 404", no_account == 404),
    ]


# ---------- ⑤ 音乐人任务进度归一化 ----------
def check_mission_normalization() -> list[tuple[str, bool]]:
    from app.browser.tasks import _normalize_mission, _split_mission_progress
    from app.repository import musician_progress_headline

    a = _normalize_mission({"description": "发布动态", "currentPeriod": 1, "periodTarget": 1})
    b = _normalize_mission({"description": "歌曲被播放≥30次", "progressDescription": "12/30",
                            "missionStatus": "ONGOING"})
    c = _normalize_mission({"name": "神秘任务"})
    d = _normalize_mission({"description": "听歌任务", "finishNum": 3, "targetNum": 5,
                            "statusName": "未完成"})

    print(f"  数值字段：{a}")
    print(f"  进度描述：{b}")
    print(f"  兜底：{c} / 状态别名：{d}")

    # 续期面板真实标题（用户提供）：进度内嵌在标题尾部
    play_raw = "近30天所有发布歌曲有效播放达650次(0/650)"
    pub_raw = "即日起30天内发布图文笔记天数≥4(4/4)"
    play_title, play_prog = _split_mission_progress(play_raw)
    pub_title, pub_prog = _split_mission_progress(pub_raw)
    no_prog = _split_mission_progress("无进度标题")

    tasks = [
        {"title": play_title, "progress": play_prog, "status": ""},
        {"title": pub_title, "progress": pub_prog, "status": "已完成"},
        {"title": "每日签到", "progress": "1/1", "status": ""},
    ]
    play_h = musician_progress_headline(tasks, ("有效播放", "播放", "听歌"))
    pub_h = musician_progress_headline(tasks, ("图文笔记", "发布动态", "发布笔记"))
    print(f"  续期面板拆分：播放={play_title!r} {play_prog}；发布={pub_prog}")
    print(f"  卡片头条：播放任务={play_h}；发布任务={pub_h}")

    return [
        ("数值字段组合出进度 1/1", a["title"] == "发布动态" and a["progress"] == "1/1"),
        ("进度描述字段优先读取", b["progress"] == "12/30" and b["status"] == "ONGOING"),
        ("字段全缺时保留标题、进度为空", c["title"] == "神秘任务" and c["progress"] == ""),
        ("finishNum/targetNum 与状态别名可用", d["progress"] == "3/5" and d["status"] == "未完成"),
        ("续期面板标题拆出播放进度 0/650", play_prog == "0/650" and "有效播放" in play_title),
        ("续期面板标题拆出发布进度 4/4", pub_prog == "4/4" and "图文笔记" in pub_title),
        ("无进度标题原样保留", no_prog == ("无进度标题", "")),
        ("播放任务含「发布」字样不会误配到发布头条", play_h == "0/650" and pub_h == "4/4"),
    ]


def main() -> int:
    checks: list[tuple[str, bool]] = []

    print("\n=== ① 密码登录错误判定（仅拦截明确凭据错误）===")
    checks += check_password_error_classifier()

    print("\n=== ② 每日任务登录态自动恢复 ===")
    checks += check_daily_auto_recovery()

    print("\n=== ③ 全局设置校验 ===")
    checks += check_settings_validation()

    print("\n=== ④ 调试截图接口路径安全 ===")
    checks += check_debug_screenshot_safety()

    print("\n=== ⑤ 音乐人任务进度归一化 ===")
    checks += check_mission_normalization()

    print("\n断言：")
    ok = True
    for desc, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {desc}")
        ok = ok and passed

    print(f"\n结论：{'通过' if ok else '未通过'}（{sum(p for _, p in checks)}/{len(checks)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
