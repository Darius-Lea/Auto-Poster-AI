"""Persistent metadata for a Facebook post containing up to ten bookings.

This ledger does not publish, delete, or automatically retry anything. A pending
or uncertain group must be reconciled before another publishing attempt.
"""
from __future__ import annotations

from datetime import datetime
import json
import re
import sqlite3
from zoneinfo import ZoneInfo


STATUSES = {"pending", "published", "uncertain", "failed"}
TITLE = "FORT WAYNE, INDIANA — ALLEN COUNTY JAIL ROSTER"
STATUS_NOTE = "(UPDATE: NO LONGER LISTED IN ALLEN COUNTY JAIL)"
DISCLAIMER = "All individuals are presumed innocent until proven guilty."
LOCAL_TIME = ZoneInfo("America/Indiana/Indianapolis")
_COLUMNS = ("group_key", "destination", "source_key", "status", "members_json",
            "original_caption", "post_id", "photo_ids_json", "error",
            "created_at", "updated_at")


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS booking_groups (
            group_key TEXT NOT NULL,
            destination TEXT NOT NULL,
            source_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'uncertain', 'failed')),
            members_json TEXT NOT NULL,
            original_caption TEXT NOT NULL,
            post_id TEXT,
            photo_ids_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_key, destination)
        )
    """)
    conn.commit()


def _identity(group_key: str, destination: str) -> tuple[str, str]:
    if not isinstance(group_key, str) or not group_key or not isinstance(destination, str) or not destination:
        raise ValueError("A group key and destination are required")
    return group_key, destination


def _members(members: list[dict]) -> list[dict]:
    if not isinstance(members, list) or not 1 <= len(members) <= 10:
        raise ValueError("A booking group must contain between one and ten members")
    required = ("hash", "name", "booking_date", "source_url", "image_url")
    hashes = set()
    for member in members:
        if not isinstance(member, dict) or any(not isinstance(member.get(key), str) or not member[key]
                                               for key in required):
            raise ValueError("Every group member needs a hash, name, booking date, source URL, and image URL")
        if member["hash"] in hashes:
            raise ValueError("Group member hashes must be distinct")
        if "caption" in member and not isinstance(member["caption"], str):
            raise ValueError("A member caption must be text")
        hashes.add(member["hash"])
    # Copy nested data too; caller mutations must never alter stored originals.
    return json.loads(json.dumps(members, ensure_ascii=False))


def _decode(row) -> dict | None:
    if row is None:
        return None
    result = dict(zip(_COLUMNS, row))
    result["members"] = json.loads(result.pop("members_json"))
    result["photo_ids"] = json.loads(result.pop("photo_ids_json"))
    result["caption"] = result["original_caption"]
    if result["photo_ids"]:
        for member, photo_id in zip(result["members"], result["photo_ids"]):
            member["photo_id"] = photo_id
    return result


def get(conn: sqlite3.Connection, group_key: str, destination: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM booking_groups WHERE group_key=? AND destination=?",
        _identity(group_key, destination),
    ).fetchone()
    return _decode(row)


def create(conn: sqlite3.Connection, group_key: str, destination: str, source_key: str,
           members: list[dict], caption: str) -> bool:
    """Reserve immutable original content; return False for an identical prior group.

    Existing failed groups still require explicit ``retry_failed``. Existing
    pending, published, or uncertain groups are never silently replaced.
    """
    identity = _identity(group_key, destination)
    if not isinstance(source_key, str) or not source_key or not isinstance(caption, str):
        raise ValueError("A source key and text caption are required")
    original = _members(members)
    payload = json.dumps(original, ensure_ascii=False, sort_keys=True)
    with conn:
        cursor = conn.execute(
            "INSERT INTO booking_groups (group_key,destination,source_key,status,members_json,original_caption) "
            "VALUES (?,?,?,'pending',?,?) ON CONFLICT(group_key,destination) DO NOTHING",
            (*identity, source_key, payload, caption),
        )
    if cursor.rowcount == 1:
        return True
    previous = get(conn, group_key, destination)
    # Read original JSON directly: published decoded members include photo IDs.
    previous_json = conn.execute(
        "SELECT members_json FROM booking_groups WHERE group_key=? AND destination=?", identity,
    ).fetchone()[0]
    if (previous["source_key"] != source_key or previous["original_caption"] != caption
            or json.loads(previous_json) != original):
        raise ValueError("A different original group already uses that key and destination")
    return False


def _reference(value: str, *, photo: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("Facebook references must be text IDs")
    value = value.removeprefix("meta:")
    pattern = r"[0-9]+" if photo else r"[0-9]+(?:_[0-9]+)?"
    if not re.fullmatch(pattern, value):
        raise ValueError("Invalid Facebook post/photo ID")
    return value


def mark_published(conn: sqlite3.Connection, group_key: str, destination: str,
                   post_id: str, photo_ids: list[str]) -> None:
    identity = _identity(group_key, destination)
    post_id = _reference(post_id)
    group = get(conn, *identity)
    if group is None or group["status"] != "pending":
        raise ValueError("Only a pending group can be confirmed published")
    if not isinstance(photo_ids, list) or len(photo_ids) != len(group["members"]):
        raise ValueError("Every group member needs its matching confirmed photo ID")
    references = [_reference(photo_id, photo=True) for photo_id in photo_ids]
    if len(set(references)) != len(references):
        raise ValueError("Group photo IDs must be distinct")
    with conn:
        cursor = conn.execute(
            "UPDATE booking_groups SET status='published',post_id=?,photo_ids_json=?,error=NULL,"
            "updated_at=CURRENT_TIMESTAMP WHERE group_key=? AND destination=? AND status='pending'",
            (post_id, json.dumps(references), *identity),
        )
        if cursor.rowcount != 1:
            raise ValueError("No pending group remains to confirm")


def _transition(conn: sqlite3.Connection, group_key: str, destination: str,
                status: str, reason: str) -> None:
    if not isinstance(reason, str) or not reason:
        raise ValueError("A failure or uncertainty reason is required")
    with conn:
        cursor = conn.execute(
            "UPDATE booking_groups SET status=?,error=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE group_key=? AND destination=? AND status='pending'",
            (status, reason, *_identity(group_key, destination)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only a pending group can change to failed or uncertain")


def mark_uncertain(conn: sqlite3.Connection, group_key: str, destination: str, reason: str) -> None:
    _transition(conn, group_key, destination, "uncertain", reason)


def fail(conn: sqlite3.Connection, group_key: str, destination: str, reason: str) -> None:
    _transition(conn, group_key, destination, "failed", reason)


def retry_failed(conn: sqlite3.Connection, group_key: str, destination: str) -> None:
    """Explicitly retry a definite rejection; ambiguous or published groups stay blocked."""
    with conn:
        cursor = conn.execute(
            "UPDATE booking_groups SET status='pending',error=NULL,updated_at=CURRENT_TIMESTAMP "
            "WHERE group_key=? AND destination=? AND status='failed'",
            _identity(group_key, destination),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only a failed group can be explicitly retried")


def list_groups(conn: sqlite3.Connection, destination: str, status: str | None = None) -> list[dict]:
    if not destination or (status is not None and status not in STATUSES):
        raise ValueError("A destination and valid optional status are required")
    query = f"SELECT {', '.join(_COLUMNS)} FROM booking_groups WHERE destination=?"
    args = [destination]
    if status is not None:
        query += " AND status=?"
        args.append(status)
    query += " ORDER BY created_at,group_key"
    return [_decode(row) for row in conn.execute(query, args).fetchall()]


def groups_for_record(conn: sqlite3.Connection, record_hash: str, destination: str) -> list[dict]:
    if not record_hash:
        raise ValueError("A record hash is required")
    return [group for group in list_groups(conn, destination, "published")
            if any(member["hash"] == record_hash for member in group["members"])]


def render_caption(members: list[dict], statuses: dict[str, str] | None = None,
                   checked_at: datetime | None = None) -> str:
    """Render each original booking separately, attaching status only to its member."""
    originals = _members(members)
    status_map = statuses or {}
    if any(status not in ("listed", "no_longer_listed") for status in status_map.values()):
        raise ValueError("Unsupported roster status")
    if checked_at is not None and checked_at.utcoffset() is None:
        raise ValueError("The roster check time must be timezone-aware")
    sections = [TITLE]
    for index, member in enumerate(originals, start=1):
        detail = member.get("caption")
        if not detail:
            detail = f"Booking date: {member['booking_date']}."
            if member.get("charge"):
                detail += f" Listed charge: {member['charge']}."
            detail += f"\nSource: {member['source_url']}"
        text = f"{index}. {member['name']}\n{detail}"
        if status_map.get(member["hash"]) == "no_longer_listed":
            text += f"\n{STATUS_NOTE}"
            if checked_at is not None:
                local = checked_at.astimezone(LOCAL_TIME)
                text += f"\nRoster checked: {local:%Y-%m-%d %I:%M %p %Z}"
        sections.append(text)
    caption = "\n\n".join(sections)
    if DISCLAIMER not in caption:
        caption += "\n\n" + DISCLAIMER
    return caption
