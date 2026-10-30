from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    account_label TEXT NOT NULL DEFAULT '',
    scheduled_time TEXT NOT NULL DEFAULT '10:05',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    auto_exchange INTEGER NOT NULL DEFAULT 0,
    reward_id INTEGER NOT NULL DEFAULT 1,
    reward_ids TEXT NOT NULL DEFAULT '[1]',
    onebot_enabled INTEGER NOT NULL DEFAULT 0,
    onebot_base_url TEXT NOT NULL DEFAULT '',
    onebot_access_token TEXT NOT NULL DEFAULT '',
    onebot_target_type TEXT NOT NULL DEFAULT 'private',
    onebot_target_id TEXT NOT NULL DEFAULT '',
    pushplus_enabled INTEGER NOT NULL DEFAULT 0,
    pushplus_token TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    last_login_nickname TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exchange_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    reward_id INTEGER NOT NULL,
    reward_name TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    package_name TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    channels TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exchange_logs_date
ON exchange_logs (run_date, created_at);

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._lock, self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            exists = connection.execute(
                "SELECT id FROM settings WHERE id = 1"
            ).fetchone()
            if not exists:
                now = _utc_now()
                connection.execute(
                    """
                    INSERT INTO settings (id, created_at, updated_at)
                    VALUES (1, ?, ?)
                    """,
                    (now, now),
                )

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(settings)")
        }
        if "reward_ids" not in columns:
            connection.execute(
                "ALTER TABLE settings "
                "ADD COLUMN reward_ids TEXT NOT NULL DEFAULT '[1]'"
            )
            connection.execute(
                """
                UPDATE settings
                SET reward_ids = '[' || reward_id || ']'
                WHERE id = 1
                """
            )

    def query_one(
        self, sql: str, params: Iterable[Any] = ()
    ) -> sqlite3.Row | None:
        with self._lock, self.connect() as connection:
            return connection.execute(sql, tuple(params)).fetchone()

    def query_all(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[sqlite3.Row]:
        with self._lock, self.connect() as connection:
            return list(connection.execute(sql, tuple(params)).fetchall())

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self.connect() as connection:
            cursor = connection.execute(sql, tuple(params))
            connection.commit()
            return int(cursor.lastrowid or 0)

    def set_runtime(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO runtime_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def get_runtime(self, key: str, default: str = "") -> str:
        row = self.query_one(
            "SELECT value FROM runtime_state WHERE key = ?",
            (key,),
        )
        return str(row["value"]) if row else default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

