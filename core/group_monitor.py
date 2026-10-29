"""Keep grouped booking posts in sync without replacing their booking text."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

from core import grouped


HEADER = "\n\nROSTER STATUS UPDATES\n"
LOCAL_TIME = ZoneInfo("America/Indiana/Indianapolis")


def initialize(conn: sqlite3.Connection) -> None:
    grouped.initialize(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS booking_group_notes (
            group_key TEXT NOT NULL,
            destination TEXT NOT NULL,
            last_note TEXT NOT NULL,
            last_states_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_key, destination)
        )
    """)
    conn.commit()


def _checkpoint(conn: sqlite3.Connection, key: str, destination: str,
                note: str, states: dict[str, str]) -> None:
    with conn:
        conn.execute("""
            INSERT INTO booking_group_notes (group_key,destination,last_note,last_states_json)
            VALUES (?,?,?,?)
            ON CONFLICT(group_key,destination) DO UPDATE SET
                last_note=excluded.last_note,
                last_states_json=excluded.last_states_json,
                updated_at=CURRENT_TIMESTAMP
        """, (key, destination, note, json.dumps(states, sort_keys=True)))


def _desired(conn: sqlite3.Connection, group: dict, destination: str,
             roster_url: str) -> tuple[dict[str, str], str]:
    states = {}
    missing_lines = []
    changed_at = []
    for index, member in enumerate(group["members"], start=1):
        row = conn.execute(
            "SELECT desired_status,status_changed_at FROM custody_posts "
            "WHERE record_hash=? AND destination=?",
            (member["hash"], destination),
        ).fetchone()
        if row is None or row[0] not in ("listed", "no_longer_listed"):
            raise ValueError("A group member has no valid custody observation; no group edit was made")
        states[member["hash"]] = row[0]
        if row[1]:
            stamp = datetime.fromisoformat(row[1])
            if stamp.utcoffset() is None:
                raise ValueError("Custody change times must be timezone-aware")
            changed_at.append(stamp.astimezone(timezone.utc))
        if row[0] == "no_longer_listed":
            if not row[1]:
                raise ValueError("A missing group member has no dated custody observation")
            name = " ".join(member["name"].split())
            booked = " ".join(member["booking_date"].split())
            missing_lines.append(
                f"{index}. {name} (booking {booked}): no longer listed in Allen County jail."
            )
    if not missing_lines:
        return states, ""
    # Use the saved observation time, not retry time: after a lost acknowledgement
    # the exact desired footer must still be recognizable on Facebook.
    checked = max(changed_at).astimezone(LOCAL_TIME)
    note = (HEADER + "\n".join(missing_lines)
            + f"\nRoster checked: {checked:%Y-%m-%d %I:%M %p %Z}"
            + f"\nSource: {roster_url}")
    return states, note


def _replacement(current: str, old_note: str, new_note: str) -> tuple[str, bool]:
    """Return the desired caption and whether it is already applied remotely."""
    if new_note and new_note in current:
        if (current.count(new_note) != 1 or current.count(HEADER) != 1
                or (old_note and old_note != new_note and old_note in current)):
            raise ValueError("Conflicting roster status notes require manual review")
        return current, True
    if old_note:
        if current.count(old_note) == 1:
            body = current.replace(old_note, "", 1)
            if HEADER in body:
                raise ValueError("An additional roster status note requires manual review")
            return body + new_note, False
        if not new_note and HEADER not in current:
            # A prior removal may have succeeded before its DB acknowledgement.
            return current, True
        raise ValueError("The stored roster status note was edited or removed; manual review is required")
    if HEADER in current:
        raise ValueError("An unrecognized roster status note requires manual review")
    return current + new_note, False


def sync(conn: sqlite3.Connection, client, source_key: str, destination: str,
         roster_url: str, now: datetime) -> dict:
    """Apply per-person status notes to existing grouped feed posts only.

    Call only after a valid full-roster observation, and never from a preview.
    Individual photo caption edits are handled by the caller's normal custody
    monitor. Original group/member captions are never changed in the ledger.
    """
    if now.utcoffset() is None:
        raise ValueError("The check time must be timezone-aware")
    if not source_key or not destination or not roster_url.startswith(("https://", "http://")):
        raise ValueError("A source key, destination, and public roster URL are required")
    initialize(conn)
    report = {"updated": 0, "errors": []}
    for group in grouped.list_groups(conn, destination, "published"):
        if group["source_key"] != source_key:
            continue
        key = group["group_key"]
        try:
            states, note = _desired(conn, group, destination, roster_url)
            saved = conn.execute(
                "SELECT last_note,last_states_json FROM booking_group_notes "
                "WHERE group_key=? AND destination=?", (key, destination),
            ).fetchone()
            old_note = saved[0] if saved else ""
            if saved and json.loads(saved[1]) == states:
                continue
            if not saved and not note:
                # An entirely listed group has no status footer to synchronize.
                _checkpoint(conn, key, destination, "", states)
                continue
            current = client.get_caption(group["post_id"])
            caption, already_applied = _replacement(current, old_note, note)
            if not already_applied and caption != current:
                client.update_caption(group["post_id"], caption, expected_caption=current)
            _checkpoint(conn, key, destination, note, states)
            report["updated"] += 1
        except Exception as exc:
            report["errors"].append(f"Group {key}: {exc}")
    return report
