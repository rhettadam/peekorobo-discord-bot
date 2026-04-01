"""Persist per-Discord-user Peekorobo API keys (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent / "user_api_keys.sqlite3"


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(_DB_PATH, check_same_thread=False)


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_keys (
                discord_user_id TEXT PRIMARY KEY,
                api_key TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_key(discord_user_id: int, api_key: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key = api_key.strip()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO user_keys (discord_user_id, api_key, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                api_key = excluded.api_key,
                updated_at = excluded.updated_at
            """,
            (str(discord_user_id), key, now),
        )
        conn.commit()
    finally:
        conn.close()


def delete_key(discord_user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM user_keys WHERE discord_user_id = ?", (str(discord_user_id),))
        conn.commit()
    finally:
        conn.close()


def get_stored_key(discord_user_id: int) -> str | None:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT api_key FROM user_keys WHERE discord_user_id = ?",
            (str(discord_user_id),),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()
