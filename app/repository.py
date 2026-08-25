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
    login_method: Optional[str] = None,
    enabled: bool = True,
) -> int:
    profile_dir = os.path.join(PROFILE_BASEDIR, _safe_phone(phone))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(phone, password, profile_dir, enabled, login_method, run_time, interval_days) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (phone, password, profile_dir, 1 if enabled else 0, login_method, run_time, interval_days),
        )
        return int(cur.lastrowid)


def update_account(account_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {
        "phone", "password", "uid", "nickname", "profile_dir", "enabled",
        "login_method", "run_time", "interval_days", "cookie_status", "last_login_at",
        "further_vip_get_time", "last_send_date", "monthly_sends", "month_tag",
        "listen_api_url", "listen_item_id", "listen_status", "listen_error",
        "listen_play_count", "listen_received_count", "listen_last_at",
        "account_role", "local_listen_enabled", "local_listen_item_id",
        "musician_play_progress", "musician_publish_progress",
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

def count_success_logs_today(account_id: int, task_type: str) -> int:
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM task_logs
            WHERE account_id=? AND task_type=? AND status='success'
              AND date(created_at, 'localtime')=date('now', 'localtime')
            """,
            (account_id, task_type),
        ).fetchone()
        return int(row["total"] or 0)

def count_success_logs_this_month(account_id: int, task_type: str) -> int:
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM task_logs
            WHERE account_id=? AND task_type=? AND status='success'
              AND strftime('%Y-%m', created_at)=strftime('%Y-%m', 'now', 'localtime')
            """,
            (account_id, task_type),
        ).fetchone()
        return int(row["total"] or 0)


# ---------- 本地账号互助听歌 ----------
def count_local_listen_successes(account_id: int, *, period: str, as_target: bool = False) -> int:
    column = "target_account_id" if as_target else "listener_account_id"
    where = {
        "today": "date(created_at)=date('now','localtime')",
        "month": "strftime('%Y-%m',created_at)=strftime('%Y-%m','now','localtime')",
    }.get(period)
    if not where:
        raise ValueError("period must be today or month")
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM local_listen_runs WHERE {column}=? AND status='success' AND {where}",
            (account_id,),
        ).fetchone()
        return int(row["n"] or 0)


def list_local_listen_targets(listener_account_id: int) -> list[dict[str, Any]]:
    """返回其他已加入本地互助的音乐人账号，优先选择今日被听较少的账号。"""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT a.*, COUNT(r.id) AS received_today
            FROM accounts a
            LEFT JOIN local_listen_runs r
              ON r.target_account_id=a.id AND r.status='success'
             AND date(r.created_at)=date('now','localtime')
            WHERE a.id<>? AND a.enabled=1 AND a.account_role='musician'
              AND a.local_listen_enabled=1
              AND COALESCE(TRIM(a.local_listen_item_id),'')<>''
            GROUP BY a.id
            ORDER BY received_today ASC, a.id ASC
            """,
            (listener_account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def local_listen_item_success_counts(target_account_id: int) -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT target_item_id, COUNT(*) AS n
            FROM local_listen_runs
            WHERE target_account_id=? AND status='success'
              AND date(created_at)=date('now','localtime')
            GROUP BY target_item_id
            """,
            (target_account_id,),
        ).fetchall()
        return {str(row["target_item_id"]): int(row["n"] or 0) for row in rows}


def add_local_listen_run(
    listener_account_id: int,
    target_account_id: int,
    target_item_id: str,
    status: str,
    message: str = "",
) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO local_listen_runs(listener_account_id,target_account_id,target_item_id,status,message) VALUES (?,?,?,?,?)",
            (listener_account_id, target_account_id, target_item_id, status, message[:2000]),
        )


# ---------- 音乐人任务快照（同步数据） ----------
def save_musician_snapshot(account_id: int, tasks: list[dict]) -> None:
    import json

    with db() as conn:
        conn.execute(
            "INSERT INTO musician_task_snapshots(account_id, payload, synced_at) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload, synced_at=excluded.synced_at",
            (account_id, json.dumps(tasks, ensure_ascii=False), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_musician_snapshot(account_id: int) -> Optional[dict]:
    import json

    with db() as conn:
        row = conn.execute(
            "SELECT payload, synced_at FROM musician_task_snapshots WHERE account_id=?",
            (account_id,),
        ).fetchone()
    if not row:
        return None
    try:
        tasks = json.loads(row["payload"])
    except ValueError:
        tasks = []
    return {"synced_at": row["synced_at"], "tasks": tasks if isinstance(tasks, list) else []}


def musician_progress_headline(tasks: list[dict], keywords: tuple[str, ...]) -> str:
    """从同步到的任务里挑出第一个匹配关键词且有进度的任务。"""
    for task in tasks or []:
        title = str(task.get("title") or "")
        if any(k in title for k in keywords):
            progress = str(task.get("progress") or "").strip()
            if progress:
                return progress
    return ""


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
