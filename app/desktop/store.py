"""Versioned, per-account local SQLite adapter, containing no central credentials."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class LocalStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError("DESKTOP_STORAGE_VERSION_UNSUPPORTED")
        if version == 0:
            with self.transaction() as db:
                db.execute("CREATE TABLE flows (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, "
                    "flow_id TEXT NOT NULL REFERENCES flows(id), document TEXT NOT NULL)"
                )
                db.execute("PRAGMA user_version=1")
        # An interrupted run is never automatically replayed. Its external
        # outcome could already have happened immediately before process exit.
        with self.transaction() as db:
            for row in db.execute("SELECT id, document FROM runs").fetchall():
                run = json.loads(row["document"])
                if run["status"] == "RUNNING":
                    run.update(status="FAILED", error="DESKTOP_INTERRUPTED", uncertain=True)
                    db.execute(
                        "UPDATE runs SET document=? WHERE id=?", (json.dumps(run), row["id"])
                    )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def all(self, table: str) -> list[dict[str, Any]]:
        if table not in {"flows", "runs"}:
            raise ValueError("Unknown local collection")
        with self._lock:
            return [
                json.loads(row[0])
                for row in self._db.execute(f"SELECT document FROM {table}").fetchall()
            ]

    def get(self, table: str, key: str) -> dict[str, Any]:
        if table not in {"flows", "runs"}:
            raise ValueError("Unknown local collection")
        with self._lock:
            row = self._db.execute(f"SELECT document FROM {table} WHERE id=?", (key,)).fetchone()
            if row is None:
                raise ValueError("DESKTOP_NOT_FOUND")
            return json.loads(row[0])

    def save_run(self, run: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute("UPDATE runs SET document=? WHERE id=?", (json.dumps(run), run["id"]))

    def close(self) -> None:
        with self._lock:
            self._db.close()
