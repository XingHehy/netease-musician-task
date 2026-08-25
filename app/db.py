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
    login_method        TEXT,          -- auto / password / qrcode
    run_time            TEXT,          -- HH:MM，空则用全局
    interval_days       INTEGER,       -- 空则用全局
    cookie_status       TEXT DEFAULT 'unknown',   -- ok / expired / unknown
    last_login_at       TEXT,
    further_vip_get_time INTEGER,      -- ms 时间戳
    last_send_date      TEXT,          -- YYYY-MM-DD
    monthly_sends       INTEGER NOT NULL DEFAULT 0,
    month_tag           TEXT,          -- YYYY-MM，用于月度计数归零
    listen_api_url      TEXT,
    listen_item_id      TEXT,
    listen_status       TEXT DEFAULT 'unconfigured',
    listen_error        TEXT,
    listen_play_count   INTEGER NOT NULL DEFAULT 0,
    listen_received_count INTEGER NOT NULL DEFAULT 0,
    listen_last_at      TEXT,
    account_role        TEXT NOT NULL DEFAULT 'musician',
    local_listen_enabled INTEGER NOT NULL DEFAULT 0,
    local_listen_item_id TEXT,
    musician_play_progress    TEXT,   -- 音乐人任务同步：被听进度（如 23/650）
    musician_publish_progress TEXT,   -- 音乐人任务同步：发布任务进度（如 4/4）
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS task_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER,
    task_type   TEXT,       -- login / musician_checkin / daily_checkin / daily / publish / vip
    status      TEXT,       -- success / fail / info
    message     TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_logs_account ON task_logs(account_id, id DESC);

CREATE TABLE IF NOT EXISTS local_listen_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listener_account_id INTEGER NOT NULL,
    target_account_id   INTEGER NOT NULL,
    target_item_id      TEXT NOT NULL,
    status              TEXT NOT NULL,
    message             TEXT,
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (listener_account_id) REFERENCES accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (target_account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_local_listen_runs_listener
    ON local_listen_runs(listener_account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_local_listen_runs_target
    ON local_listen_runs(target_account_id, created_at);

CREATE TABLE IF NOT EXISTS musician_task_snapshots (
    account_id INTEGER PRIMARY KEY,
    payload    TEXT NOT NULL,
    synced_at  TEXT NOT NULL
);
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
        account_columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)")}
        if "login_method" not in account_columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN login_method TEXT")
        listen_columns = {
            "listen_api_url": "TEXT",
            "listen_item_id": "TEXT",
            "listen_status": "TEXT DEFAULT 'unconfigured'",
            "listen_error": "TEXT",
            "listen_play_count": "INTEGER NOT NULL DEFAULT 0",
            "listen_received_count": "INTEGER NOT NULL DEFAULT 0",
            "listen_last_at": "TEXT",
            "account_role": "TEXT NOT NULL DEFAULT 'musician'",
            "local_listen_enabled": "INTEGER NOT NULL DEFAULT 0",
            "local_listen_item_id": "TEXT",
            "musician_play_progress": "TEXT",
            "musician_publish_progress": "TEXT",
        }
        for name, definition in listen_columns.items():
            if name not in account_columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
        for k, v in SETTINGS_SEED.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )
