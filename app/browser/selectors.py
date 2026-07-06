"""网易云音乐相关的 URL、CSS 选择器、任务名常量。集中管理，便于维护。"""

from __future__ import annotations

# ---- URL ----
LOGIN_URL = "https://music.163.com/#/login?targetUrl=https%3A%2F%2Fmusic.163.com%2Fst%2Fmusician"
MUSICIAN_HOME_URL = "https://music.163.com/musician/artist/home"
FRIEND_URL = "https://music.163.com/#/friend"
VIP_RIGHT_URL = "https://y.music.163.com/g/yida/7d4d0e9f89884a68b8eddea50b5aa6a6"

# ---- 接口 URL 片段（用于监听 response）----
CYCLE_LIST_API = "/weapi/nmusician/workbench/mission/cycle/list"
REWARD_OBTAIN_API = "/weapi/nmusician/workbench/mission/reward/obtain/new"
DAILY_TASK_API = "/weapi/point/dailyTask"
SHARE_API = "/weapi/share/friends/resource"
EVENT_DELETE_API = "/weapi/event/delete"
VIP_INFO_URL_SUBSTR = "nmusician/workbench/special/right/vip/info"
SCAN_APPLY_API = "/weapi/login/origin-device/scan-apply/start"

# ---- 登录选择器 ----
SEL_OTHER_LOGIN = "选择其他登录模式"
SEL_TERMS = "#j-official-terms"
SEL_PHONE_ENTRY = "a:has(div:has-text('手机号登录/注册'))"
SEL_PWD_LOGIN = "密码登录"
SEL_PHONE_INPUT = "input[placeholder='请输入手机号']"
SEL_PWD_INPUT = "input[placeholder='请输入密码']"
SEL_LOGIN_BTN = "a:has(div:has-text('登录'))"

# ---- 滑块 ----
SEL_YIDUN_MODAL = ".yidun_modal__body, .yidun.yidun-custom"
SEL_YIDUN_BG = "img.yidun_bg-img"
SEL_YIDUN_JIGSAW = "img.yidun_jigsaw"
SEL_YIDUN_SLIDER = ".yidun_slider__icon"
SEL_YIDUN_REFRESH = ".yidun_refresh"
SEL_YIDUN_SMS = ".yidun_smsbox, .yidun_voice"

# ---- 二次验证 ----
SEL_SECONDARY_MODAL = ".mrc-modal-container"
SEL_SECONDARY_OPTION = ".mjZhxAab"
SEL_SECONDARY_OPTION_TEXT = "span.DwyRKeOe"

# ---- 发布动态 ----
SEL_PUB_EVENT = "#pubEvent"
SEL_NOTE_TEXTAREA = "textarea.u-txt.area.j-flag[placeholder='一起聊聊吧~']"
SEL_ADD_MUSIC = "给笔记配上音乐"
SEL_MUSIC_SEARCH = ".m-lysearch input.u-txt.txt.j-flag"
SEL_SEARCH_RESULT = ".srchlist li.sitm"
SEL_SHARE_BTN = "a.u-btn2.u-btn2-2.u-btn2-w2.j-flag[data-action='share']"

# ---- VIP ----
SEL_VIP_CONTAINER = "div.vip-container"
SEL_VIP_CHECK = "span.check"

# ---- 常量 ----
NETWORK_SECURITY_RISK_TEXT = "您当前的网络环境存在安全风险"
VIP_TASK_NAME = "即日起30天内发布图文笔记天数≥4"

# ---- 反检测 init script ----
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    delete window.cdc_asyncScript;
    delete window.cdc_file;
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    window.chrome = { runtime: {} };
"""
