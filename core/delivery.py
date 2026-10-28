"""Per-destination delivery state, including uploads needing manual reconciliation.

An interrupted or ambiguous upload stays blocked until the Page is checked. This
avoids automatically resending a photo that the remote service may have accepted.
The historical ``seen`` table is deliberately left untouched.
"""
from __future__ import annotations

import sqlite3


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deliveries (
            record_hash TEXT NOT NULL,
            destination TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'uncertain')),
            remote_ref TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (record_hash, destination)
        )
    """)
    conn.commit()


def _identity(record_hash: str, destination: str) -> tuple[str, str]:
    if not record_hash or not destination:
        raise ValueError("A record hash and destination are required")
    return record_hash, destination


def blocked(conn: sqlite3.Connection, record_hash: str, destination: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM deliveries WHERE record_hash = ? AND destination = ? "
        "AND status IN ('pending', 'published', 'uncertain')",
        _identity(record_hash, destination),
    ).fetchone() is not None


def claim(conn: sqlite3.Connection, record_hash: str, destination: str) -> bool:
    """Persist a reservation before upload; only one claimant can succeed."""
    cursor = conn.execute(
        "INSERT INTO deliveries (record_hash, destination, status) VALUES (?, ?, 'pending') "
        "ON CONFLICT(record_hash, destination) DO NOTHING",
        _identity(record_hash, destination),
    )
    conn.commit()
    return cursor.rowcount == 1


def complete(conn: sqlite3.Connection, record_hash: str, destination: str, ref: str) -> None:
    if not ref:
        raise ValueError("A confirmed remote reference is required")
    cursor = conn.execute(
        "UPDATE deliveries SET status = 'published', remote_ref = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE record_hash = ? AND destination = ? AND status = 'pending'",
        (ref, *_identity(record_hash, destination)),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise ValueError("No pending delivery to complete")


def release(conn: sqlite3.Connection, record_hash: str, destination: str) -> None:
    """Allow a retry after a definite failure; never erase published/uncertain state."""
    conn.execute(
        "DELETE FROM deliveries WHERE record_hash = ? AND destination = ? AND status = 'pending'",
        _identity(record_hash, destination),
    )
    conn.commit()


def uncertain(conn: sqlite3.Connection, record_hash: str, destination: str) -> None:
    cursor = conn.execute(
        "UPDATE deliveries SET status = 'uncertain', updated_at = CURRENT_TIMESTAMP "
        "WHERE record_hash = ? AND destination = ? AND status = 'pending'",
        _identity(record_hash, destination),
    )
    conn.commit()
    if cursor.rowcount != 1:
        raise ValueError("No pending delivery to mark uncertain")


def list_unresolved(conn: sqlite3.Connection) -> list[dict]:
    columns = ("record_hash", "destination", "status", "remote_ref", "updated_at")
    rows = conn.execute(
        "SELECT record_hash, destination, status, remote_ref, updated_at FROM deliveries "
        "WHERE status IN ('pending', 'uncertain') ORDER BY updated_at, record_hash, destination"
    ).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def resolve(conn: sqlite3.Connection, record_hash: str, destination: str,
            remote_ref: str | None = None, retry: bool = False) -> None:
    """Reconcile after checking the destination: record its ID OR explicitly retry.

    The caller must obtain confirmation that the Page was checked before passing
    ``retry=True``. This function never performs a remote request itself.
    """
    if bool(remote_ref) == bool(retry):
        raise ValueError("Supply either a confirmed remote reference or an explicit retry")
    identity = _identity(record_hash, destination)
    if retry:
        cursor = conn.execute(
            "DELETE FROM deliveries WHERE record_hash = ? AND destination = ? "
            "AND status IN ('pending', 'uncertain')", identity,
        )
    else:
        cursor = conn.execute(
            "UPDATE deliveries SET status = 'published', remote_ref = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE record_hash = ? AND destination = ? AND status IN ('pending', 'uncertain')",
            (remote_ref, *identity),
        )
    conn.commit()
    if cursor.rowcount != 1:
        raise ValueError("No unresolved delivery matches that record and destination")
