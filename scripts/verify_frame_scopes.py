#!/usr/bin/env python3
"""验证跨 frame 遍历时「先 count() 再等待」的必要性。

背景：helpers.scopes() 会产出 main frame + 所有子 frame。若调用方直接对每个
scope 使用等待型 API（wait_for_function / wait_for 等），那么每一个「存活但不
含目标元素」的 frame 都会死等满一整份 timeout。helpers.py 里的 click_first /
fill_first / check_first 都已先用不带等待的 count() 预检再等待，但
login.solve_slider 内部的 wait_real_image 漏了这一步，于是在网易云登录页
（含多个 iframe）上每轮滑块尝试会白白多花 frame 数 × 10 秒。

用法（不联网、不需要账号，只用项目已有的 playwright）：
    python scripts/verify_frame_scopes.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import Page, sync_playwright  # noqa: E402

from app.browser.helpers import scopes  # noqa: E402

WAIT_TIMEOUT_MS = 10000  # 与 solve_slider 里 wait_real_image 的默认 timeout 一致
TARGET = "img.yidun_bg-img"  # 与 selectors.SEL_YIDUN_BG 一致
N_DECOY = 5  # 不含验证码的干扰 iframe 数量

# 一个干扰 iframe + 一个真正含验证码图的 iframe，模拟网易云登录页的结构
PAGE_HTML = """<!doctype html><html><body>
<div id="box"></div>
<script>
  const decoy = "<html><body><p>无关内容</p></body></html>";
  const real  = "<html><body><img class='yidun_bg-img' width='320' height='160' " +
                "src='data:image/svg+xml;base64," +
                btoa("<svg xmlns='http://www.w3.org/2000/svg' width='320' height='160'>" +
                     "<rect width='320' height='160' fill='#888'/></svg>") + "'></body></html>";
  const box = document.getElementById('box');
  for (let i = 0; i < DECOY_N; i++) {
    const f = document.createElement('iframe'); f.srcdoc = decoy; box.appendChild(f);
  }
  const f = document.createElement('iframe'); f.srcdoc = real; box.appendChild(f);
</script></body></html>""".replace("DECOY_N", str(N_DECOY))


def wait_real_image(scope, selector: str, min_width: int = 120,
                    timeout: int = WAIT_TIMEOUT_MS) -> None:
    """与 login.solve_slider 内部的同名函数保持一致。"""
    scope.wait_for_function(
        f"""() => {{
            const img = document.querySelector("{selector}");
            return img && img.complete && img.naturalWidth > {min_width};
        }}""",
        timeout=timeout,
    )


def sweep(page: Page, *, guard: bool) -> tuple[float, int, bool]:
    """模拟 solve_slider 对每个 scope 找验证码图的过程。

    guard=False 复现修复前行为；guard=True 为修复后（先 count() 预检）。
    """
    started = time.time()
    waited_on = 0
    found = False
    for scope in scopes(page):
        try:
            if guard and scope.locator(TARGET).count() == 0:
                continue
            waited_on += 1
            wait_real_image(scope, TARGET)
            found = True
            break
        except Exception:  # noqa: BLE001  超时是修复前的预期行为
            continue
    return time.time() - started, waited_on, found


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content(PAGE_HTML)
        page.wait_for_timeout(800)

        n_frames = len([f for f in page.frames if f is not page.main_frame])
        print(f"构造页面：{n_frames} 个子 iframe（{N_DECOY} 个不含验证码 + 1 个含验证码）")
        print(f"scopes() 产出 {len(list(scopes(page)))} 个 scope"
              f"（含 main frame），单次等待 timeout={WAIT_TIMEOUT_MS}ms\n")

        t_before, waited_before, found_before = sweep(page, guard=False)
        print(f"修复前（直接等待）  : 在 {waited_before} 个 scope 上等待，"
              f"耗时 {t_before:6.2f}s，找到验证码={found_before}")

        t_after, waited_after, found_after = sweep(page, guard=True)
        print(f"修复后（先 count()）: 在 {waited_after} 个 scope 上等待，"
              f"耗时 {t_after:6.2f}s，找到验证码={found_after}")

        browser.close()

    expected_waste = N_DECOY * WAIT_TIMEOUT_MS / 1000
    print("\n断言：")
    checks = [
        ("两种方式都能定位到验证码所在 frame", found_before and found_after),
        (f"修复前在无关 scope 上白等（{waited_before} 个 > 1）", waited_before > 1),
        ("修复后只在真正含验证码的 scope 上等待（1 个）", waited_after == 1),
        (f"修复后耗时 {t_after:.2f}s < 2s", t_after < 2.0),
        (f"修复前耗时 {t_before:.2f}s ≈ 无关 frame 数 × timeout（约 {expected_waste:.0f}s）",
         t_before > expected_waste * 0.9),
    ]
    ok = True
    for desc, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {desc}")
        ok = ok and passed

    print(f"\n结论：{'通过' if ok else '未通过'}，"
          f"单轮滑块尝试节省约 {t_before - t_after:.1f}s")
    print("说明：网易云登录页实际含多个 iframe，线上日志表现为连续 6 条 "
          "`Frame.wait_for_function: Timeout 10000ms exceeded.`，"
          "单轮浪费约 60s，导致 3 次重试实际只兑现 1 次真实滑块机会。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
