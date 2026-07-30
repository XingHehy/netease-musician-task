#!/usr/bin/env python3
"""复现并验证 issue #22：二次验证弹窗出现但二维码永远拿不到、日志每 3 秒刷屏。

用本地伪造的「登录安全验证」弹窗跑两个确定性场景，对比修复前后行为。
修复前的行为通过传 qr_state=None 且不挂常驻监听器来还原（这正是原实现）。

用法（不联网、不需要账号，只用项目已有的 playwright）：
    python scripts/verify_secondary_qr.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import Page, sync_playwright  # noqa: E402

from app.browser import login as L  # noqa: E402
from app.browser import selectors as S  # noqa: E402
from app.event_bus import bus  # noqa: E402

API_URL = "https://music.163.com" + S.SCAN_APPLY_API
POLL_ROUNDS = 6  # 精简版等待循环轮数（真实代码是 SECONDARY_WAIT_SECONDS / 3s）
RETRY_AFTER = 2  # 精简版重试间隔（真实代码是 QR_RETRY_INTERVAL = 15s）

# 伪造弹窗：class 名与 selectors.py 中的常量一致。
# 首次点击「原设备扫码验证」不触发接口，第二次才触发——对应线上「点了没反应，
# 而代码又永远不会再点一次」的情形。
FIXTURE = """<!doctype html><html><body>
<div class="mrc-modal-container">
  <div class="mjZhxAab"><span class="DwyRKeOe">原设备扫码验证</span></div>
  <div class="mjZhxAab"><span class="DwyRKeOe">短信验证码验证</span></div>
</div>
<script>
  window.__clicks = 0;
  document.querySelectorAll('.mjZhxAab')[0].addEventListener('click', () => {
    window.__clicks += 1;
    if (window.__clicks >= 2) {
      fetch("API_URL", {method: "POST"}).then(r => r.text());
    }
  });
</script></body></html>""".replace("API_URL", API_URL)

SCENARIOS = {
    "retry_needed": {
        "desc": "重试后接口才返回 token（修复后应当拿到二维码）",
        "body": '{"code":200,"data":{"pollingToken":"FAKE-TOKEN-1"}}',
        "qr_after": 1,
    },
    "no_token": {
        "desc": "接口始终不返回 pollingToken（修复后应当给出可操作提示而非静默刷屏）",
        "body": '{"code":200,"data":{}}',
        "qr_after": 0,
    },
}


def build_page(browser, body: str) -> Page:
    page = browser.new_page()
    page.route(
        "**" + S.SCAN_APPLY_API,
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )
    page.goto("https://music.163.com/fixture")
    page.set_content(FIXTURE)
    return page


def run(page: Page, account_id: int, *, fixed: bool) -> dict:
    """跑一轮「首次检测 + 等待循环」；fixed=False 还原修复前行为。"""
    qr_state = L.new_qr_state() if fixed else None
    if fixed:
        page.on("response", L.make_scan_response_hook(qr_state))

    started = time.time()
    L.check_secondary_verification(page, account_id, timeout=10, qr_state=qr_state)

    last_retry = time.time()
    for _ in range(POLL_ROUNDS):
        if fixed and not qr_state["pushed"] and qr_state["token"]:
            L._push_scan_qr(account_id, qr_state["token"], qr_state)
        L.check_secondary_verification(
            page, account_id, timeout=2, auto_action=False, qr_state=qr_state
        )
        if fixed and not qr_state["pushed"] and time.time() - last_retry >= RETRY_AFTER:
            last_retry = time.time()
            L.check_secondary_verification(
                page, account_id, timeout=3, auto_action=True, qr_state=qr_state
            )
        time.sleep(0.5)
    elapsed = time.time() - started

    buf = bus.get_buffer(account_id)
    logs = [m["line"] for m in buf if m.get("type") == "log"]
    qrs = [m["qr_url"] for m in buf if m.get("type") == "qrcode"]
    bus.clear_buffer(account_id)
    return {
        "qr_count": len(qrs),
        "modal_log_count": len([x for x in logs if "检测到登录安全验证弹窗" in x]),
        "retry_hint": any("稍后自动重试" in x for x in logs),
        "clicks": page.evaluate("window.__clicks"),
        "elapsed": elapsed,
    }


def main() -> int:
    results: dict[str, dict[str, dict]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for i, (mode, cfg) in enumerate(SCENARIOS.items()):
            print(f"\n=== 场景「{mode}」：{cfg['desc']} ===")
            results[mode] = {}
            for fixed in (False, True):
                page = build_page(browser, cfg["body"])
                r = run(page, account_id=9000 + i * 2 + int(fixed), fixed=fixed)
                page.close()
                label = "修复后" if fixed else "修复前"
                results[mode][label] = r
                print(f"  {label}：推送二维码 {r['qr_count']} 次，"
                      f"弹窗日志 {r['modal_log_count']} 条，"
                      f"点击 {r['clicks']} 次，耗时 {r['elapsed']:.1f}s")
        browser.close()

    print("\n断言：")
    checks = []
    for mode, cfg in SCENARIOS.items():
        before, after = results[mode]["修复前"], results[mode]["修复后"]
        checks += [
            (f"[{mode}] 修复前从不重试抓取（点击 {before['clicks']} 次）",
             before["clicks"] == 1),
            (f"[{mode}] 修复后会重试抓取（点击 {after['clicks']} 次 > 1）",
             after["clicks"] > 1),
            (f"[{mode}] 修复前弹窗日志刷屏（{before['modal_log_count']} 条）",
             before["modal_log_count"] > 2),
            (f"[{mode}] 修复后弹窗日志只播报一次（{after['modal_log_count']} 条）",
             after["modal_log_count"] == 1),
            (f"[{mode}] 修复后二维码推送次数 = {cfg['qr_after']}",
             after["qr_count"] == cfg["qr_after"]),
        ]
    # 拿不到 token 时必须给出可操作提示，而不是静默
    checks.append(
        ("[no_token] 修复后输出「稍后自动重试」提示", results["no_token"]["修复后"]["retry_hint"])
    )
    checks.append(
        ("[retry_needed] 修复前拿不到二维码（0 次）",
         results["retry_needed"]["修复前"]["qr_count"] == 0)
    )

    ok = True
    for desc, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {desc}")
        ok = ok and passed

    print(f"\n结论：{'通过' if ok else '未通过'}")
    print("说明：本脚本验证「二维码能否被拿到并推送」「失败时是否给出可操作提示」"
          "「日志是否刷屏」；真机扫码确认那一步无法在无账号环境下自动化。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
