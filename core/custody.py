"""Daily source-roster observations for retained Facebook booking posts.

Roster disappearance is recorded as ``no_longer_listed``, never as proof of
release. This module performs no network requests and never deletes a post.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import sqlite3


MIN_SCAN_INTERVAL = timedelta(hours=20)
STATUSES = {"listed", "no_longer_listed"}


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS custody_posts (
            record_hash TEXT NOT NULL,
            destination TEXT NOT NULL,
            source_key TEXT NOT NULL,
            remote_ref TEXT NOT NULL,
            original_caption TEXT NOT NULL,
            source_url TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            desired_status TEXT NOT NULL DEFAULT 'listed',
            applied_status TEXT NOT NULL DEFAULT 'listed',
            missing_checks INTEGER NOT NULL DEFAULT 0,
            missing_since TEXT,
            last_seen_at TEXT,
            last_observed_at TEXT,
            status_changed_at TEXT,
            applied_at TEXT,
            PRIMARY KEY (record_hash, destination)
        );
        CREATE TABLE IF NOT EXISTS custody_scans (
            source_key TEXT PRIMARY KEY,
            checked_at TEXT NOT NULL,
            member_count INTEGER NOT NULL
        );
    """)
    conn.commit()


def _utc(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("Observation time must be timezone-aware")
    return now.astimezone(timezone.utc)


def track(conn: sqlite3.Connection, record_hash: str, destination: str,
          source_key: str, remote_ref: str, caption: str, source_url: str,
          name: str = "") -> None:
    """Track a caller-confirmed Meta delivery, retaining its first caption.

    The caller must only register confirmed published deliveries. A different
    post or source cannot replace an existing monitored identity by accident.
    """
    if not record_hash or not source_key or not source_url:
        raise ValueError("Record, source key, and source URL are required")
    if not destination.startswith("meta:") or not re.fullmatch(
            r"(?:meta:)?[0-9]+(?:_[0-9]+)?", remote_ref):
        raise ValueError("Custody tracking requires a confirmed Facebook post/photo reference")
    reference = remote_ref.removeprefix("meta:")
    existing = conn.execute(
        "SELECT source_key, remote_ref FROM custody_posts WHERE record_hash = ? AND destination = ?",
        (record_hash, destination),
    ).fetchone()
    if existing and tuple(existing) != (source_key, reference):
        raise ValueError("A different source or Facebook post is already tracked for this identity")
    with conn:
        conn.execute("""
            INSERT INTO custody_posts
                (record_hash, destination, source_key, remote_ref, original_caption, source_url, name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_hash, destination) DO UPDATE SET
                source_url = excluded.source_url,
                name = excluded.name
        """, (record_hash, destination, source_key, reference, caption, source_url, name))


def pending_updates(conn: sqlite3.Connection, source_key: str) -> list[dict]:
    columns = ("record_hash", "destination", "source_key", "remote_ref", "original_caption",
               "source_url", "name", "target_status", "missing_since", "last_seen_at",
               "last_observed_at", "status_changed_at")
    rows = conn.execute("""
        SELECT record_hash, destination, source_key, remote_ref, original_caption,
               source_url, name, desired_status, missing_since, last_seen_at,
               last_observed_at, status_changed_at
        FROM custody_posts
        WHERE source_key = ? AND desired_status != applied_status
        ORDER BY record_hash, destination
    """, (source_key,)).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def observe(conn: sqlite3.Connection, source_key: str, members: set[str],
            now: datetime) -> list[dict]:
    """Accept a complete daily snapshot and derive safe caption-update work.

    Empty snapshots and declines exceeding 20% require investigation. Two
    accepted missing observations, at least 20 hours apart, are needed before
    recording disappearance. Rejected or too-frequent scans change no history.
    """
    current = _utc(now)
    if not source_key:
        raise ValueError("A source key is required")
    members = set(members)
    if not members or any(not isinstance(member, str) or not member for member in members):
        raise ValueError("Refusing an empty or invalid roster snapshot")
    previous = conn.execute(
        "SELECT checked_at, member_count FROM custody_scans WHERE source_key = ?", (source_key,)
    ).fetchone()
    if previous:
        if len(members) * 5 < previous[1] * 4:
            raise ValueError("Roster count dropped more than 20%; investigate before updating posts")
        if current - datetime.fromisoformat(previous[0]) < MIN_SCAN_INTERVAL:
            return []
    stamp = current.isoformat()
    tracked = conn.execute("""
        SELECT record_hash, destination, desired_status, missing_checks, missing_since
        FROM custody_posts WHERE source_key = ?
    """, (source_key,)).fetchall()
    with conn:
        for record_hash, destination, desired, missing, missing_since in tracked:
            present = record_hash in members
            new_missing = 0 if present else missing + 1
            target = "listed" if present else ("no_longer_listed" if new_missing >= 2 else desired)
            conn.execute("""
                UPDATE custody_posts SET
                    desired_status = ?, missing_checks = ?, missing_since = ?,
                    last_seen_at = CASE WHEN ? THEN ? ELSE last_seen_at END,
                    last_observed_at = ?,
                    status_changed_at = CASE WHEN desired_status != ? THEN ? ELSE status_changed_at END
                WHERE record_hash = ? AND destination = ?
            """, (target, new_missing, None if present else (missing_since or stamp),
                  present, stamp, stamp, target, stamp, record_hash, destination))
        conn.execute("""
            INSERT INTO custody_scans (source_key, checked_at, member_count) VALUES (?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                checked_at = excluded.checked_at, member_count = excluded.member_count
        """, (source_key, stamp, len(members)))
    return pending_updates(conn, source_key)


def mark_applied(conn: sqlite3.Connection, record_hash: str, destination: str,
                 status: str, now: datetime) -> None:
    """Acknowledge an existing-post edit only after the remote call succeeds."""
    if status not in STATUSES:
        raise ValueError("Unsupported custody status")
    stamp = _utc(now).isoformat()
    with conn:
        cursor = conn.execute("""
            UPDATE custody_posts SET applied_status = ?, applied_at = ?
            WHERE record_hash = ? AND destination = ? AND desired_status = ?
        """, (status, stamp, record_hash, destination, status))
        if cursor.rowcount != 1:
            raise ValueError("No tracked post has that desired status")
