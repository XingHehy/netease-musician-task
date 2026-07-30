#!/usr/bin/env python3
"""验证真实账号登录时暴露的三个回归（2026-07-30 实测日志）。

真实日志里的失败链：滑块其实已通过并弹出「登录安全验证」，但
  1) 松手后只看固定 2 秒那一瞬 → 判定滑块失败；
  2) 去点 .yidun_refresh，而易盾正在拆掉验证码 → 默认 30s 超时烧光重试预算，
     第 2、3 次尝试在 0 秒内空转；
  3) 判定失败后降级扫码，但降级分支被 `not use_qr` 挡住，安全验证弹窗完全没人处理；
  4) 扫码分支用 page.goto(LOGIN_URL) 想「重新加载」，而 LOGIN_URL 是 hash 路由，
     goto 同 URL 不会重载文档 → 页面一直停在弹窗上，二维码永远不出现。

本脚本用本地伪造页面 + AST 结构检查覆盖这四点，全部离线、不需要账号，
只用项目已有的 playwright。

用法：
    python scripts/verify_login_recovery.py
"""

from __future__ import annotations

import ast
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.browser import login as L  # noqa: E402
from app.browser import selectors as S  # noqa: E402

LOGIN_FIXTURE = """<!doctype html><html><body>
<div id="view">login form</div>
</body></html>"""

# 验证码在 REDRAW_AFTER_MS 之后才出现，模拟易盾失败后重建 iframe 的空窗期
CAPTCHA_FIXTURE = """<!doctype html><html><body>
<div class="yidun_modal__body">
  <div id="holder"></div>
  <button class="yidun_refresh" style="visibility:hidden">刷新</button>
</div>
<script>
  setTimeout(() => {
    document.getElementById('holder').innerHTML =
      '<img class="yidun_bg-img" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">';
  }, REDRAW_AFTER_MS);
</script></body></html>"""


def check_hash_route_reload(browser) -> list[tuple[str, bool]]:
    """LOGIN_URL 是 hash 路由：goto 同 URL 不重载文档，_open_login_page 必须 reload。"""
    hits: list[str] = []
    page = browser.new_page()
    page.route(
        "https://music.163.com/**",
        lambda route: (
            hits.append(route.request.url),
            route.fulfill(status=200, content_type="text/html", body=LOGIN_FIXTURE),
        )[-1],
    )

    # 首次打开
    page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    loads_after_first = len(hits)

    # 修复前：直接 goto 同一个 URL
    page.evaluate("window.__marker = 'alive'")
    page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    before_marker = page.evaluate("window.__marker")
    before_loads = len(hits) - loads_after_first

    # 修复后：_open_login_page
    page.evaluate("window.__marker = 'alive'")
    L._open_login_page(page)
    after_marker = page.evaluate("window.__marker")
    after_loads = len(hits) - loads_after_first - before_loads
    page.close()

    print(f"  修复前 goto 同 URL：新文档请求 {before_loads} 次，"
          f"window.__marker={before_marker!r}（未重载则残留）")
    print(f"  修复后 _open_login_page：新文档请求 {after_loads} 次，"
          f"window.__marker={after_marker!r}（重载则被清空）")

    return [
        ("修复前 goto 同 hash URL 不产生新文档请求", before_loads == 0),
        ("修复前页面状态残留（marker 未被清空）", before_marker == "alive"),
        ("修复后 _open_login_page 触发新文档请求", after_loads == 1),
        ("修复后页面状态被清空（marker 消失）", after_marker is None),
    ]


def check_captcha_redraw_wait(browser) -> list[tuple[str, bool]]:
    """验证码重建期间：单次 count() 会漏判，_wait_captcha_present 会等到它回来。"""
    redraw_ms = 1500
    body = CAPTCHA_FIXTURE.replace("REDRAW_AFTER_MS", str(redraw_ms))

    # 修复前：进 scopes() 就 count()==0 → continue，本轮 0 秒空转
    page = browser.new_page()
    page.set_content(body)
    t0 = time.time()
    before_found = any(
        sc.locator(S.SEL_YIDUN_BG).count() > 0 for sc in L.scopes(page)
    )
    before_elapsed = time.time() - t0
    page.close()

    # 修复后：轮询等待重绘
    page = browser.new_page()
    page.set_content(body)
    t0 = time.time()
    after_found = L._wait_captcha_present(page, L.SLIDER_REDRAW_WAIT)
    after_elapsed = time.time() - t0
    page.close()

    print(f"  修复前单次 count()：找到={before_found}，耗时 {before_elapsed:.2f}s"
          f"（验证码 {redraw_ms}ms 后才出现）")
    print(f"  修复后轮询等待：找到={after_found}，耗时 {after_elapsed:.2f}s")

    return [
        ("修复前漏判正在重建的验证码", before_found is False),
        ("修复前本轮几乎 0 秒空转", before_elapsed < 0.5),
        ("修复后等到验证码重绘并找到", after_found is True),
        ("修复后等待时长合理（介于重绘时间与上限之间）",
         redraw_ms / 1000 <= after_elapsed < L.SLIDER_REDRAW_WAIT),
    ]


def check_refresh_click_timeout(browser) -> list[tuple[str, bool]]:
    """刷新按钮被易盾隐藏时，点击必须短超时失败，而不是默认 30s 烧光重试预算。"""
    page = browser.new_page()
    page.set_content(CAPTCHA_FIXTURE.replace("REDRAW_AFTER_MS", "999999"))

    t0 = time.time()
    err: Exception | None = None
    try:
        page.locator(S.SEL_YIDUN_REFRESH).first.click(timeout=L.SLIDER_REFRESH_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        err = e
    elapsed = time.time() - t0
    page.close()

    raw_lines = len(str(err).splitlines()) if err else 0
    brief = L._brief(err) if err else ""
    print(f"  隐藏的刷新按钮：{L.SLIDER_REFRESH_TIMEOUT}ms 超时，实际 {elapsed:.2f}s "
          f"（默认超时为 30000ms）")
    print(f"  异常原始行数 {raw_lines} → _brief() 后 1 行：{brief[:70]}")

    return [
        ("隐藏的刷新按钮会点击失败", err is not None),
        (f"失败耗时被限制在 {L.SLIDER_REFRESH_TIMEOUT}ms 量级（实测 {elapsed:.2f}s < 10s）",
         elapsed < 10),
        ("短超时远小于默认 30s", L.SLIDER_REFRESH_TIMEOUT < 30000),
        (f"Playwright 异常带 call log（{raw_lines} 行），_brief() 压成 1 行",
         raw_lines > 1 and "\n" not in brief),
    ]


def check_secondary_gate() -> list[tuple[str, bool]]:
    """结构检查：login_account 里的二次验证必须由 attempted_password 门控。

    这一段的正确性依赖真实的服务端弹窗，无法离线跑通完整 login_account，
    所以用 AST 锁住「不能再退回 not use_qr」这个回归点。
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "browser", "login.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "login_account")

    gate_node = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.BoolOp):
            continue
        calls = [c for c in ast.walk(node.test)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                 and c.func.id == "check_secondary_verification"]
        if calls and not any(k.arg == "auto_action" for c in calls for k in c.keywords):
            gate_node = node
            break

    if gate_node is None:
        return [("找到二次验证的入口门控", False)]

    operands = [ast.dump(v) for v in gate_node.test.values]
    gated_by_attempted = any("attempted_password" in o and "UnaryOp" not in o
                             for o in operands)
    gated_by_not_use_qr = any("use_qr" in o and "Not()" in o for o in operands)
    # 弹窗出现时必须撤销滑块假阴性触发的降级
    body_dump = ast.dump(ast.Module(body=gate_node.body, type_ignores=[]))
    cancels_fallback = "use_qr" in body_dump and "取消扫码降级" in ast.get_source_segment(
        src, gate_node) if ast.get_source_segment(src, gate_node) else False

    print(f"  门控条件：{' and '.join(ast.unparse(v) for v in gate_node.test.values)}")
    print(f"  弹窗分支内是否撤销扫码降级：{cancels_fallback}")

    return [
        ("二次验证由 attempted_password 门控", gated_by_attempted),
        ("二次验证不再由 not use_qr 门控（滑块假阴性时也会处理弹窗）",
         not gated_by_not_use_qr),
        ("检测到弹窗后会撤销滑块失败触发的扫码降级", bool(cancels_fallback)),
    ]


def main() -> int:
    checks: list[tuple[str, bool]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])

        print("\n=== ① hash 路由下「重新加载登录页」是否真的重载 ===")
        checks += check_hash_route_reload(browser)

        print("\n=== ② 滑块重试：验证码重建期间是否空转 ===")
        checks += check_captcha_redraw_wait(browser)

        print("\n=== ③ 刷新按钮不可见时的点击超时 ===")
        checks += check_refresh_click_timeout(browser)

        browser.close()

    print("\n=== ④ 二次验证门控（AST 结构检查）===")
    checks += check_secondary_gate()

    print("\n断言：")
    ok = True
    for desc, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {desc}")
        ok = ok and passed

    print(f"\n结论：{'通过' if ok else '未通过'}（{sum(p for _, p in checks)}/{len(checks)}）")
    print("说明：①②③ 为行为验证，④ 为结构验证——二次验证的完整时序依赖网易服务端"
          "下发的安全验证弹窗，无法离线复现，故只锁住导致该 bug 的门控条件。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
