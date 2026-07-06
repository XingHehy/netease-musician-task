"""账号 CRUD。删除账号时可选择是否一并删除浏览器 profile 目录。"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo
from app.logging_conf import logger

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    phone: str
    password: str
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool = True


class AccountUpdate(BaseModel):
    password: str | None = None
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool | None = None


def _safe(acc: dict) -> dict:
    """对外隐藏密码。"""
    out = dict(acc)
    out.pop("password", None)
    return out


@router.get("")
def list_accounts() -> list[dict]:
    return [_safe(a) for a in repo.list_accounts()]


@router.get("/{account_id}")
def get_account(account_id: int) -> dict:
    acc = repo.get_account(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return _safe(acc)


@router.post("")
def create_account(body: AccountCreate) -> dict:
    if repo.get_account_by_phone(body.phone):
        raise HTTPException(400, "该手机号已存在")
    account_id = repo.create_account(
        body.phone,
        body.password,
        run_time=body.run_time,
        interval_days=body.interval_days,
        enabled=body.enabled,
    )
    _reschedule()
    return _safe(repo.get_account(account_id))


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate) -> dict:
    if not repo.get_account(account_id):
        raise HTTPException(404, "账号不存在")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    repo.update_account(account_id, **fields)
    _reschedule()
    return _safe(repo.get_account(account_id))


@router.delete("/{account_id}")
def delete_account(account_id: int, delete_profile: bool = False) -> dict:
    acc = repo.get_account(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    profile_dir = acc.get("profile_dir")
    repo.delete_account(account_id)
    removed = False
    if delete_profile and profile_dir:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            removed = True
            logger.info(f"已删除浏览器 profile 目录：{profile_dir}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"删除 profile 目录失败：{e}")
    _reschedule()
    return {"ok": True, "profile_removed": removed}


def _reschedule() -> None:
    try:
        from app.scheduler import reschedule_all

        reschedule_all()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"重排调度失败：{e}")
