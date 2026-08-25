"""全局设置读写。"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo

router = APIRouter(prefix="/api/settings", tags=["settings"])

_EDITABLE = {
    "default_send_time",
    "execution_interval_days",
    "max_monthly_sends",
    "local_listen_daily_max",
    "local_listen_monthly_max",
    "local_listen_play_percent",
    "local_listen_start_time",
    "headless",
    "login_method",
    "wecom_webhook_key",
    "custom_webhook_url",
    "custom_webhook_method",
    "custom_webhook_headers",
    "custom_webhook_body",
    "notification_method",
    "pushplus_token",
    "pushplus_topic",
}

_TIME_KEYS = {"default_send_time", "local_listen_start_time"}
_NON_NEGATIVE_INT_KEYS = {
    "execution_interval_days",
    "max_monthly_sends",
    "local_listen_daily_max",
    "local_listen_monthly_max",
}
_URL_KEYS = {"custom_webhook_url"}
_NOTIFICATION_METHODS = {"none", "wecom", "pushplus", "custom"}
_LOGIN_METHODS = {"auto", "password", "qrcode"}


def _validate_setting(key: str, value: str) -> None:
    """逐项校验非法值；合法历史配置不受影响。空字符串按「未配置」放行。"""
    if key in _TIME_KEYS and value:
        try:
            hour, minute = value.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, f"{key} 格式必须为 HH:MM") from exc
    elif key in _NON_NEGATIVE_INT_KEYS:
        try:
            if int(value) < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, f"{key} 必须是非负整数") from exc
    elif key == "local_listen_play_percent":
        try:
            if not 34 <= int(value) <= 100:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, "播放比例必须在 34 到 100 之间") from exc
    elif key in _URL_KEYS and value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(422, f"{key} 必须是 http 或 https URL")
    elif key == "headless" and value not in ("0", "1"):
        raise HTTPException(422, "headless 只能是 0 或 1")
    elif key == "login_method" and value not in _LOGIN_METHODS:
        raise HTTPException(422, "login_method 必须是 auto / password / qrcode")
    elif key == "notification_method" and value not in _NOTIFICATION_METHODS:
        raise HTTPException(422, "notification_method 必须是 none / wecom / pushplus / custom")
    elif key == "custom_webhook_method" and value not in ("GET", "POST"):
        raise HTTPException(422, "custom_webhook_method 只能是 GET 或 POST")


class SettingsUpdate(BaseModel):
    values: dict[str, str]


@router.get("")
def get_settings() -> dict:
    values = repo.get_all_settings()
    values.pop("admin_password_hash", None)
    return values


@router.put("")
def update_settings(body: SettingsUpdate) -> dict:
    for k, v in body.values.items():
        if k in _EDITABLE:
            _validate_setting(k, str(v))
            repo.set_setting(k, str(v))
    # 时间/开关变更后重排调度
    try:
        from app.scheduler import reschedule_all

        reschedule_all()
    except Exception:
        pass
    return get_settings()
