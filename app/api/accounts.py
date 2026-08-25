"""账号 CRUD。删除账号时可选择是否一并删除浏览器 profile 目录。"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo
from app.logging_conf import logger
from app.account_identity import account_label
from app.local_listen import parse_item_ids

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    phone: str
    password: str = ""
    login_method: str | None = None
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool = True
    account_role: str = "musician"
    local_listen_enabled: bool = False
    local_listen_item_id: str = ""


class AccountUpdate(BaseModel):
    password: str | None = None
    login_method: str | None = None
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool | None = None
    account_role: str | None = None
    local_listen_enabled: bool | None = None
    local_listen_item_id: str | None = None


def _safe(acc: dict) -> dict:
    """对外隐藏密码。"""
    out = dict(acc)
    out.pop("password", None)
    account_id = int(out["id"])
    out["local_listen_helped_today"] = repo.count_local_listen_successes(account_id, period="today")
    out["local_listen_received_today"] = repo.count_local_listen_successes(account_id, period="today", as_target=True)
    out["local_listen_helped_month"] = repo.count_local_listen_successes(account_id, period="month")
    out["local_listen_received_month"] = repo.count_local_listen_successes(account_id, period="month", as_target=True)
    if out.get("account_role", "musician") == "musician":
        # 进度由「同步」写入账号列；synced_at 仍取快照表
        out["musician_play_progress"] = out.get("musician_play_progress") or ""
        out["musician_publish_progress"] = out.get("musician_publish_progress") or ""
        snapshot = repo.get_musician_snapshot(account_id)
        out["musician_synced_at"] = (snapshot or {}).get("synced_at") or ""
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
    if body.account_role not in {"musician", "player"}:
        raise HTTPException(422, "账号角色必须是 musician 或 player")
    try:
        parse_item_ids(body.local_listen_item_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    account_id = repo.create_account(
        body.phone,
        body.password,
        login_method=body.login_method,
        run_time=body.run_time,
        interval_days=body.interval_days,
        enabled=body.enabled,
    )
    repo.update_account(
        account_id,
        account_role=body.account_role,
        local_listen_enabled=1 if body.local_listen_enabled else 0,
        local_listen_item_id="" if body.account_role == "player" else body.local_listen_item_id.strip(),
    )
    _reschedule()
    return _safe(repo.get_account(account_id))


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate) -> dict:
    if not repo.get_account(account_id):
        raise HTTPException(404, "账号不存在")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "account_role" in fields and fields["account_role"] not in {"musician", "player"}:
        raise HTTPException(422, "账号角色必须是 musician 或 player")
    if "account_role" in fields and fields["account_role"] == "player":
        fields["local_listen_item_id"] = ""
    if fields.get("local_listen_item_id"):
        try:
            parse_item_ids(fields["local_listen_item_id"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if "local_listen_enabled" in fields:
        fields["local_listen_enabled"] = 1 if fields["local_listen_enabled"] else 0
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
            logger.info(f"已删除账号 {account_label(account_id, account=acc)} 的浏览器 profile 目录")
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
