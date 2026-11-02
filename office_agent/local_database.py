"""Local SQLite: settings, selected printers, recent activity, and the job-recovery table (used from Phase 5).
Lives in the per-user data folder and survives upgrades. Never stores document contents or clear-text secrets."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import MAX_ACTIVITY_ROWS

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalDatabase:
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("The local database was created by a newer Sesh Agent. Install the latest version.")
        if version < 1:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selected_printers (windows_name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                                                     level TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, stage TEXT NOT NULL, printer TEXT,
                                                 detail TEXT, updated_at TEXT NOT NULL);
            """)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    def _run(self, sql: str, params: tuple = ()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value) -> None:
        self._run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._run("DELETE FROM settings WHERE key=?", (k,))

    def selected_printers(self) -> list[str]:
        with self._lock:
            return [r["windows_name"] for r in self._conn.execute("SELECT windows_name FROM selected_printers ORDER BY windows_name")]

    def set_selected_printers(self, names: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM selected_printers")
            self._conn.executemany("INSERT OR IGNORE INTO selected_printers(windows_name) VALUES(?)", [(n,) for n in names])
            self._conn.commit()

    def log(self, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO activity(ts,level,message) VALUES(?,?,?)", (_now(), level, message[:500]))
            self._conn.execute("DELETE FROM activity WHERE id <= (SELECT MAX(id) FROM activity) - ?", (MAX_ACTIVITY_ROWS,))
            self._conn.commit()

    def recent_activity(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT ts,level,message FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
