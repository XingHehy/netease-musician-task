"""
应用配置：全部从环境变量读取，带默认值。不依赖旧的 config.py / Redis。
所有运行期数据都落在 app/data 下。
"""

from __future__ import annotations

import os

# ---- 路径 ----
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("APP_DATA_DIR", os.path.join(APP_DIR, "data"))
PROFILE_BASEDIR = os.getenv("PLAYWRIGHT_PROFILE_BASEDIR", os.path.join(DATA_DIR, "profiles"))
DB_PATH = os.getenv("APP_DB_PATH", os.path.join(DATA_DIR, "app.db"))
LOG_DIR = os.getenv("APP_LOG_DIR", os.path.join(DATA_DIR, "log"))
DEBUG_DIR = os.getenv("APP_DEBUG_DIR", os.path.join(DATA_DIR, "debug"))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PROFILE_BASEDIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ---- Web 服务 ----
HOST = os.getenv("APP_HOST", "0.0.0.0")
PORT = int(os.getenv("APP_PORT", "8000"))

# ---- 浏览器 ----
# 默认无头（headless=1）；本地想看真实窗口时设 PLAYWRIGHT_HEADLESS=0
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip() not in ("0", "false", "False", "")
# 调试模式：登录/任务出现异常或风控时，把页面截图存到 APP_DEBUG_DIR/{手机号}/
DEBUG_SCREENSHOT = os.getenv("PLAYWRIGHT_DEBUG_SCREENSHOT", "1").strip() not in ("0", "false", "False", "")
USER_AGENT = os.getenv(
    "PLAYWRIGHT_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
# 浏览器操作默认超时（毫秒）
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))

# ---- 调度默认值（全局，可被账号级覆盖）----
DEFAULT_SEND_TIME = os.getenv("SEND_TIME", "09:30")  # HH:MM
DEFAULT_INTERVAL_DAYS = int(os.getenv("EXECUTION_INTERVAL_DAYS", "3"))
MAX_MONTHLY_SENDS = int(os.getenv("MAX_MONTHLY_SENDS", "4"))

# ---- 通知（首次播种到 settings 表；运行期以 settings 表为准）----
WECOM_WEBHOOK_KEY = os.getenv("WECOM_WEBHOOK_KEY", "")
CUSTOM_WEBHOOK_URL = os.getenv("CUSTOM_WEBHOOK_URL", "")
CUSTOM_WEBHOOK_METHOD = os.getenv("CUSTOM_WEBHOOK_METHOD", "POST")
CUSTOM_WEBHOOK_HEADERS = os.getenv("CUSTOM_WEBHOOK_HEADERS", "")
CUSTOM_WEBHOOK_BODY = os.getenv("CUSTOM_WEBHOOK_BODY", "")

# settings 表首次播种用的默认全局设置
SETTINGS_SEED = {
    "default_send_time": DEFAULT_SEND_TIME,
    "execution_interval_days": str(DEFAULT_INTERVAL_DAYS),
    "max_monthly_sends": str(MAX_MONTHLY_SENDS),
    "headless": "1" if HEADLESS else "0",
    "wecom_webhook_key": WECOM_WEBHOOK_KEY,
    "custom_webhook_url": CUSTOM_WEBHOOK_URL,
    "custom_webhook_method": CUSTOM_WEBHOOK_METHOD,
    "custom_webhook_headers": CUSTOM_WEBHOOK_HEADERS,
    "custom_webhook_body": CUSTOM_WEBHOOK_BODY,
}
