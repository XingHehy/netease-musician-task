"""SQLite 连接与建表。使用标准库 sqlite3，线程安全靠每次操作新建连接。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH, SETTINGS_SEED

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phone               TEXT NOT NULL UNIQUE,
    password            TEXT NOT NULL,
    uid                 TEXT,
    nickname            TEXT,
    profile_dir         TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    run_time            TEXT,          -- HH:MM，空则用全局
    interval_days       INTEGER,       -- 空则用全局
    cookie_status       TEXT DEFAULT 'unknown',   -- ok / expired / unknown
    last_login_at       TEXT,
    further_vip_get_time INTEGER,      -- ms 时间戳
    last_send_date      TEXT,          -- YYYY-MM-DD
    monthly_sends       INTEGER NOT NULL DEFAULT 0,
    month_tag           TEXT,          -- YYYY-MM，用于月度计数归零
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER,
    task_type   TEXT,       -- login / checkin / daily / publish / vip
    status      TEXT,       -- success / fail / info
    message     TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_logs_account ON task_logs(account_id, id DESC);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表并播种全局 settings（仅首次）。"""
    with db() as conn:
        conn.executescript(SCHEMA)
        for k, v in SETTINGS_SEED.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )
