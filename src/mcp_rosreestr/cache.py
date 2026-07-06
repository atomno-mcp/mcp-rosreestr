"""Persistent SQLite cache for Rosreestr lookups.

The schema mirrors SPEC §8.1 in ``_knowledge/specs/spec.md``. We use
``sqlite3`` from the standard library (no async — all calls are quick;
the cache is only consulted from short tool invocations) and store
typed Python objects via JSON serialisation for portability.

Two TTLs are used:

* **live**: cadastral cost, encumbrances — refreshed every 24 h.
* **static**: year built, area, address — refreshed every 7 days.

The TTL is computed by the tool layer; this module just records
``cache_until`` and skips expired rows on read.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cache_objects (
    cadastral_number TEXT PRIMARY KEY,
    payload_json     TEXT NOT NULL,
    source           TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    cache_until      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_objects_cache_until
    ON cache_objects(cache_until);

CREATE TABLE IF NOT EXISTS cache_addresses (
    address_query    TEXT NOT NULL,
    object_types     TEXT NOT NULL DEFAULT '[]',
    payload_json     TEXT NOT NULL,
    source           TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    cache_until      TEXT NOT NULL,
    PRIMARY KEY (address_query, object_types)
);
CREATE INDEX IF NOT EXISTS idx_cache_addresses_cache_until
    ON cache_addresses(cache_until);

CREATE TABLE IF NOT EXISTS cadastral_value_history (
    cadastral_number TEXT NOT NULL,
    value_date       TEXT NOT NULL,
    value_rub        REAL NOT NULL,
    basis_act        TEXT,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (cadastral_number, value_date)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tool_name     TEXT NOT NULL,
    args_hash     TEXT NOT NULL,
    cache_hit     INTEGER NOT NULL,
    upstream_used TEXT,
    took_ms       INTEGER,
    success       INTEGER NOT NULL,
    error_class   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    return str(value)


class SqliteCache:
    """Thread-safe persistent cache.

    Connection is opened lazily; we keep a single connection per process
    guarded by a lock because ``sqlite3.Connection`` is not safe to share
    between threads by default.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path: Path = Path(path) if path else Path("./mcp_rosreestr_cache.sqlite")
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # --------------------------------------------------------- lifecycle

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO cache_meta(key,value) VALUES (?,?)",
            ("schema_version", SCHEMA_VERSION),
        )
        conn.execute(
            "INSERT OR IGNORE INTO cache_meta(key,value) VALUES (?,?)",
            ("created_at", _utc_now_iso()),
        )
        conn.commit()
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            if self._conn is None:
                self._conn = self._connect()
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ----------------------------------------------------------- objects

    def get_object(self, cadastral_number: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT payload_json, source, fetched_at, cache_until"
                " FROM cache_objects WHERE cadastral_number = ? AND cache_until > ?",
                (cadastral_number, now),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        payload["_cache"] = {
            "source": row["source"],
            "fetched_at": row["fetched_at"],
            "cache_until": row["cache_until"],
        }
        return payload

    def set_object(
        self,
        cadastral_number: str,
        payload: dict[str, Any],
        *,
        source: str,
        cache_until: datetime | str,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO cache_objects"
                " (cadastral_number, payload_json, source, fetched_at, cache_until)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    cadastral_number,
                    json.dumps(payload, default=str, ensure_ascii=False),
                    source,
                    _utc_now_iso(),
                    _to_iso(cache_until),
                ),
            )

    # --------------------------------------------------------- addresses

    def get_address(
        self,
        address_query: str,
        object_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        types_key = json.dumps(sorted(object_types or []))
        now = _utc_now_iso()
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT payload_json, source, fetched_at, cache_until FROM cache_addresses"
                " WHERE address_query = ? AND object_types = ? AND cache_until > ?",
                (address_query, types_key, now),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return None

    def set_address(
        self,
        address_query: str,
        payload: dict[str, Any],
        *,
        source: str,
        cache_until: datetime | str,
        object_types: list[str] | None = None,
    ) -> None:
        types_key = json.dumps(sorted(object_types or []))
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO cache_addresses"
                " (address_query, object_types, payload_json, source, fetched_at, cache_until)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    address_query,
                    types_key,
                    json.dumps(payload, default=str, ensure_ascii=False),
                    source,
                    _utc_now_iso(),
                    _to_iso(cache_until),
                ),
            )

    # ----------------------------------------------------- value history

    def add_value_point(
        self,
        cadastral_number: str,
        value_date: str,
        value_rub: float,
        basis_act: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO cadastral_value_history"
                " (cadastral_number, value_date, value_rub, basis_act, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (cadastral_number, value_date, value_rub, basis_act, _utc_now_iso()),
            )

    def get_value_history(self, cadastral_number: str) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT value_date, value_rub, basis_act FROM cadastral_value_history"
                " WHERE cadastral_number = ? ORDER BY value_date ASC",
                (cadastral_number,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------- audit log

    def log_call(
        self,
        *,
        tool_name: str,
        args_hash: str,
        cache_hit: bool,
        upstream_used: str | None,
        took_ms: int | None,
        success: bool,
        error_class: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log"
                " (ts, tool_name, args_hash, cache_hit, upstream_used, took_ms, success, error_class)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    tool_name,
                    args_hash,
                    int(cache_hit),
                    upstream_used,
                    took_ms,
                    int(success),
                    error_class,
                ),
            )

    # ------------------------------------------------------- maintenance

    def prune(self) -> int:
        """Remove expired rows. Returns the number of rows removed."""
        now = _utc_now_iso()
        with self._cursor() as cur:
            cur.execute("DELETE FROM cache_objects WHERE cache_until <= ?", (now,))
            removed = cur.rowcount
            cur.execute("DELETE FROM cache_addresses WHERE cache_until <= ?", (now,))
            removed += cur.rowcount
        return max(0, removed)

    def stats(self) -> dict[str, int]:
        with self._cursor() as cur:
            objects_count = cur.execute("SELECT COUNT(*) FROM cache_objects").fetchone()[0]
            addresses_count = cur.execute("SELECT COUNT(*) FROM cache_addresses").fetchone()[0]
            audit_count = cur.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        return {
            "objects": int(objects_count),
            "addresses": int(addresses_count),
            "audit_entries": int(audit_count),
        }


__all__ = ["SqliteCache", "SCHEMA_VERSION"]
