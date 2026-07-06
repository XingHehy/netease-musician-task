"""accounts / task_logs / settings 的数据访问层。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from app.config import PROFILE_BASEDIR
from app.db import db


# ---------- settings ----------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def get_setting_int(key: str, default: int) -> int:
    """读取整型配置（settings 表实时值），解析失败回退 default。"""
    val = get_setting(key, None)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def get_setting_bool(key: str, default: bool) -> bool:
    """读取布尔型配置（settings 表实时值）。"""
    val = get_setting(key, None)
    if val is None:
        return default
    return str(val) not in ("0", "false", "False", "")


def get_all_settings() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ---------- accounts ----------
def _safe_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone) if c.isdigit())
    return digits or str(phone)


def list_accounts() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_account(account_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None


def get_account_by_phone(phone: str) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
        return dict(row) if row else None


def create_account(
    phone: str,
    password: str,
    *,
    run_time: Optional[str] = None,
    interval_days: Optional[int] = None,
    enabled: bool = True,
) -> int:
    profile_dir = os.path.join(PROFILE_BASEDIR, _safe_phone(phone))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(phone, password, profile_dir, enabled, run_time, interval_days) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (phone, password, profile_dir, 1 if enabled else 0, run_time, interval_days),
        )
        return int(cur.lastrowid)


def update_account(account_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {
        "phone", "password", "uid", "nickname", "profile_dir", "enabled",
        "run_time", "interval_days", "cookie_status", "last_login_at",
        "further_vip_get_time", "last_send_date", "monthly_sends", "month_tag",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    vals.append(account_id)
    with db() as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", vals)


def delete_account(account_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.execute("DELETE FROM task_logs WHERE account_id=?", (account_id,))


# ---------- task_logs ----------
def add_log(account_id: Optional[int], task_type: str, status: str, message: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO task_logs(account_id, task_type, status, message) VALUES (?, ?, ?, ?)",
            (account_id, task_type, status, message[:2000]),
        )


def list_logs(account_id: Optional[int] = None, limit: int = 100) -> list[dict[str, Any]]:
    with db() as conn:
        if account_id is not None:
            rows = conn.execute(
                "SELECT * FROM task_logs WHERE account_id=? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
