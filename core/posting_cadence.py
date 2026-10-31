"""Persistent cooldowns for scheduled case notices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

CASE_INTERVAL = timedelta(hours=24)
CASE_KEY = "fort-wayne-open-case"


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posting_cadence (
            cadence_key TEXT PRIMARY KEY,
            last_attempt_at TEXT NOT NULL
        )
    """)
    conn.commit()


def is_due(conn: sqlite3.Connection, key: str = CASE_KEY,
           now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("Cadence time must be timezone-aware")
    row = conn.execute(
        "SELECT last_attempt_at FROM posting_cadence WHERE cadence_key=?", (key,)
    ).fetchone()
    if not row:
        return True
    previous = datetime.fromisoformat(row[0])
    return current.astimezone(timezone.utc) - previous.astimezone(timezone.utc) >= CASE_INTERVAL


def reserve_attempt(conn: sqlite3.Connection, key: str = CASE_KEY,
                    now: datetime | None = None) -> None:
    """Start the 24-hour window before upload so ambiguous responses cannot double-post."""
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("Cadence time must be timezone-aware")
    stamp = current.astimezone(timezone.utc).isoformat()
    with conn:
        conn.execute("""
            INSERT INTO posting_cadence (cadence_key, last_attempt_at) VALUES (?, ?)
            ON CONFLICT(cadence_key) DO UPDATE SET last_attempt_at=excluded.last_attempt_at
        """, (key, stamp))


def clear_failed_attempt(conn: sqlite3.Connection, key: str = CASE_KEY) -> None:
    """Clear a reservation only after the publisher confirms the post was rejected."""
    with conn:
        conn.execute("DELETE FROM posting_cadence WHERE cadence_key=?", (key,))
