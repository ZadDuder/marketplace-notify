from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    account_slug TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS error_alerts (
                    key TEXT PRIMARY KEY,
                    last_sent_at INTEGER NOT NULL,
                    count INTEGER NOT NULL
                )
                """
            )

    def claim_event(
        self,
        event_key: str,
        source: str,
        account_slug: str | None = None,
        payload: Any | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO events(event_key, source, account_slug, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_key, source, account_slug, payload_json, now),
            )
            return cur.rowcount == 1

    def has_event(self, event_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM events WHERE event_key = ?", (event_key,)).fetchone()
            return row is not None

    def get_value(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_value(self, key: str, value: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO kv(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def was_notification_recent(self, key: str, cooldown_seconds: int) -> bool:
        raw_value = self.get_value(f"notification:{key}")
        if raw_value is None:
            return False
        try:
            return int(time.time()) - int(raw_value) < cooldown_seconds
        except ValueError:
            return False

    def mark_notification(self, key: str) -> None:
        self.set_value(f"notification:{key}", str(int(time.time())))

    def should_send_error(self, key: str, cooldown_seconds: int = 3600) -> bool:
        now = int(time.time())
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_sent_at, count FROM error_alerts WHERE key = ?",
                (key,),
            ).fetchone()
            if row and now - int(row["last_sent_at"]) < cooldown_seconds:
                conn.execute(
                    "UPDATE error_alerts SET count = count + 1 WHERE key = ?",
                    (key,),
                )
                return False

            if row:
                conn.execute(
                    "UPDATE error_alerts SET last_sent_at = ?, count = count + 1 WHERE key = ?",
                    (now, key),
                )
            else:
                conn.execute(
                    "INSERT INTO error_alerts(key, last_sent_at, count) VALUES (?, ?, ?)",
                    (key, now, 1),
                )
            return True
