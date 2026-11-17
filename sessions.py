"""SQLite storage for Instagram conversation state and generated baskets.

The bot flow spans two separate webhook deliveries: the user shares a reel, then
taps a store. The analysed recipe has to survive between them, and the finished
basket has to outlive the conversation so the hosted basket page keeps working.
SQLite gives us both without adding a dependency or a Redis instance.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time


DEFAULT_DB_PATH = "refill.db"
SESSION_TTL_SECONDS = int(os.getenv("REFILL_SESSION_TTL_SECONDS", "21600"))
BASKET_TTL_SECONDS = int(os.getenv("REFILL_BASKET_TTL_SECONDS", "604800"))
SEEN_EVENT_TTL_SECONDS = int(os.getenv("REFILL_SEEN_EVENT_TTL_SECONDS", "86400"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS baskets (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    store TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_events (
    event_id TEXT PRIMARY KEY,
    seen_at REAL NOT NULL
);
"""

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None


def get_db_path() -> str:
    return os.getenv("REFILL_DB_PATH", DEFAULT_DB_PATH)


def configure(db_path: str) -> None:
    """Point storage at a different database. Used by tests."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None
        os.environ["REFILL_DB_PATH"] = db_path


def _conn() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        path = get_db_path()
        _connection = sqlite3.connect(path, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        if path != ":memory:":
            _connection.execute("PRAGMA journal_mode=WAL")
        _connection.executescript(SCHEMA)
        _connection.commit()
    return _connection


def save_session(user_id: str, state: str, payload: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO sessions (user_id, state, payload, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            (user_id, state, json.dumps(payload), time.time()),
        )
        conn.commit()


def load_session(user_id: str) -> dict | None:
    with _lock:
        row = _conn().execute(
            "SELECT state, payload, updated_at FROM sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        return None
    if time.time() - row["updated_at"] > SESSION_TTL_SECONDS:
        clear_session(user_id)
        return None

    return {"state": row["state"], "payload": json.loads(row["payload"])}


def clear_session(user_id: str) -> None:
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()


def create_basket(user_id: str, store: str, payload: dict) -> str:
    token = secrets.token_urlsafe(9)
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO baskets (token, user_id, store, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, store, json.dumps(payload), time.time()),
        )
        conn.commit()
    return token


def load_basket(token: str) -> dict | None:
    with _lock:
        row = _conn().execute(
            "SELECT user_id, store, payload, created_at FROM baskets WHERE token = ?",
            (token,),
        ).fetchone()

    if row is None or time.time() - row["created_at"] > BASKET_TTL_SECONDS:
        return None

    return {
        "store": row["store"],
        "created_at": row["created_at"],
        **json.loads(row["payload"]),
    }


def claim_event(event_id: str) -> bool:
    """Return True the first time an event id is seen.

    Meta redelivers webhook events when a response is slow or non-200, so every
    handler has to be idempotent.
    """
    if not event_id:
        return True

    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO seen_events (event_id, seen_at) VALUES (?, ?)",
                (event_id, time.time()),
            )
        except sqlite3.IntegrityError:
            return False
        conn.execute(
            "DELETE FROM seen_events WHERE seen_at < ?",
            (time.time() - SEEN_EVENT_TTL_SECONDS,),
        )
        conn.commit()
    return True
