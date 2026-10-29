"""Persist whether grouped catch-up should pause normal individual posting.

Delivery and group ledgers remain authoritative for what was actually posted.
Starting another session changes only this control row, never delivery history.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catchup_controls (
            destination TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'paused')),
            report_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def get(conn: sqlite3.Connection, destination: str) -> dict | None:
    row = conn.execute(
        "SELECT destination,session_id,status,report_json,updated_at "
        "FROM catchup_controls WHERE destination=?",
        (_required(destination, "Destination"),),
    ).fetchone()
    if row is None:
        return None
    return {"destination": row[0], "session_id": row[1], "status": row[2],
            "report": json.loads(row[3]), "updated_at": row[4]}


def begin(conn: sqlite3.Connection, destination: str, session_id: str) -> dict:
    """Start a new authorized run, or leave the same session entirely unchanged."""
    destination = _required(destination, "Destination")
    session_id = _required(session_id, "Session ID")
    with conn:
        conn.execute("""
            INSERT INTO catchup_controls (destination,session_id,status,report_json,updated_at)
            VALUES (?,?,'active','{}',?)
            ON CONFLICT(destination) DO UPDATE SET
                session_id=excluded.session_id, status='active', report_json='{}',
                updated_at=excluded.updated_at
            WHERE catchup_controls.session_id != excluded.session_id
        """, (destination, session_id, _stamp()))
    return get(conn, destination)


def finish_batch(conn: sqlite3.Connection, destination: str, session_id: str,
                 report: dict) -> dict:
    """Save the batch's real outcome; errors always take priority over completion."""
    destination = _required(destination, "Destination")
    session_id = _required(session_id, "Session ID")
    if not isinstance(report, dict):
        raise ValueError("The catch-up report must be an object")
    for field in ("complete", "paused"):
        if field in report and not isinstance(report[field], bool):
            raise ValueError(f"Report {field} must be a boolean")
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False)
    status = ("paused" if report.get("errors") or report.get("paused")
              else "completed" if report.get("complete") else "active")
    with conn:
        cursor = conn.execute("""
            UPDATE catchup_controls SET status=?,report_json=?,updated_at=?
            WHERE destination=? AND session_id=?
        """, (status, payload, _stamp(), destination, session_id))
        if cursor.rowcount != 1:
            raise ValueError("The catch-up session does not match the current destination session")
    return get(conn, destination)


def blocks_normal(conn: sqlite3.Connection, destination: str) -> bool:
    current = get(conn, destination)
    return current is not None and current["status"] in ("active", "paused")
