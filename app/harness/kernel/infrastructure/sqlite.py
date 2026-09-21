from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SQLiteRepository:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise ValueError(f"Unsupported framework database schema version {version}")
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS framework_records (
                tenant TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY (tenant, kind, id)
            );
            CREATE TABLE IF NOT EXISTS framework_events (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
                run TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS framework_event_scope
                ON framework_events(tenant, run, cursor);
            PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def tenants(self) -> list[str]:
        with self.lock:
            return [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT tenant FROM framework_records ORDER BY tenant",
                ).fetchall()
            ]

    def get(self, tenant: str, kind: str, key: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT body FROM framework_records WHERE tenant=? AND kind=? AND id=?",
                (tenant, kind, key),
            ).fetchone()
            return json.loads(row["body"]) if row else None

    def put(self, tenant: str, kind: str, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO framework_records VALUES(?,?,?,?) "
                "ON CONFLICT(tenant,kind,id) DO UPDATE SET body=excluded.body",
                (tenant, kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
            )

    def scan(self, tenant: str, kind: str) -> Iterator[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT body FROM framework_records WHERE tenant=? AND kind=? ORDER BY id",
                (tenant, kind),
            ).fetchall()
        return iter(json.loads(row["body"]) for row in rows)

    def event(self, tenant: str, run: str, kind: str, data: dict[str, Any]) -> int:
        with self.lock:
            cursor = self.connection.execute(
                "INSERT INTO framework_events(tenant,run,kind,body,created) VALUES(?,?,?,?,?)",
                (tenant, run, kind, json.dumps(data, ensure_ascii=False, default=str), time.time()),
            )
            return cursor.lastrowid

    def events(
        self, tenant: str, run: str, after: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM framework_events WHERE tenant=? AND run=? AND cursor>? "
                "ORDER BY cursor LIMIT ?",
                (tenant, run, after, min(limit, 1000)),
            ).fetchall()
        return [{**dict(row), "body": json.loads(row["body"])} for row in rows]

    def close(self) -> None:
        self.connection.close()
