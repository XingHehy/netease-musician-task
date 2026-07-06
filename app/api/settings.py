"""全局设置读写。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app import repository as repo

router = APIRouter(prefix="/api/settings", tags=["settings"])

_EDITABLE = {
    "default_send_time",
    "execution_interval_days",
    "max_monthly_sends",
    "headless",
    "wecom_webhook_key",
    "custom_webhook_url",
    "custom_webhook_method",
    "custom_webhook_headers",
    "custom_webhook_body",
}


class SettingsUpdate(BaseModel):
    values: dict[str, str]


@router.get("")
def get_settings() -> dict:
    return repo.get_all_settings()


@router.put("")
def update_settings(body: SettingsUpdate) -> dict:
    for k, v in body.values.items():
        if k in _EDITABLE:
            repo.set_setting(k, str(v))
    # 时间/开关变更后重排调度
    try:
        from app.scheduler import reschedule_all

        reschedule_all()
    except Exception:
        pass
    return repo.get_all_settings()
