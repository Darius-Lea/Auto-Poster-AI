"""Validate and import an anonymous, browser-created setup after owner authentication.

The public wizard never writes to this database. Imported setups are inert
configuration records. Neither payment nor import can start automation.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

PLATFORMS = {
    "facebook": "Facebook Page",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
    "threads": "Threads",
    "pinterest": "Pinterest",
    "x": "X",
}
FREQUENCIES = {"once", "daily", "twice_daily", "three_daily", "weekly"}
SOURCES = {"own_posts", "ai_drafts", "approved_feed"}
APPROVALS = {"approve_every_post", "auto_when_supported"}
HEX_ID = re.compile(r"^[a-f0-9]{32}$")


def connect():
    db = sqlite3.connect(os.getenv("AUTOPOST_WEB_DB", "autopost_web.db"), timeout=12)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    return db


def init():
    with connect() as c:
        c.execute("""
          CREATE TABLE IF NOT EXISTS imported_guest_setups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            setup_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            imported_at INTEGER NOT NULL,
            UNIQUE(workspace_id,setup_key)
          )
        """)


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid setup data")
    key = raw.get("setup_key")
    if not isinstance(key, str) or not HEX_ID.fullmatch(key):
        raise ValueError("Your setup has an invalid identifier")
    platforms = raw.get("platforms")
    if (not isinstance(platforms, list) or not platforms or len(platforms) > 8
            or len(set(platforms)) != len(platforms)
            or any(not isinstance(p, str) or p not in PLATFORMS for p in platforms)):
        raise ValueError("Choose valid social platforms")
    message = raw.get("message")
    topic = raw.get("topic")
    if not isinstance(message, str) or not 2 <= len(message.strip()) <= 10000:
        raise ValueError("Write a message for your post")
    if not isinstance(topic, str) or len(topic) > 300:
        raise ValueError("The description is too long")
    freq = raw.get("frequency")
    if freq not in FREQUENCIES:
        raise ValueError("Choose a posting frequency")
    source = raw.get("source")
    if source not in SOURCES:
        raise ValueError("Choose a valid content source")
    approval = raw.get("approval")
    if approval not in APPROVALS:
        raise ValueError("Choose how posts should be reviewed")
    time_type = raw.get("time_type")
    if time_type not in {"after_activation", "at_time"}:
        raise ValueError("Choose when posting should begin")
    when = raw.get("scheduled_at", "")
    if time_type == "at_time":
        if not isinstance(when, str) or len(when) > 42:
            raise ValueError("Choose a valid posting date")
        try:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if dt.tzinfo is None or abs(dt.utcoffset().total_seconds()) > 14 * 3600:
                raise ValueError
        except (ValueError, OverflowError, AttributeError):
            raise ValueError("Choose a date with an explicit timezone") from None
        when = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    else:
        when = ""
    return {
        "setup_key": key,
        "platforms": platforms,
        "message": message.strip(),
        "topic": topic.strip(),
        "frequency": freq,
        "source": source,
        "approval": approval,
        "time_type": time_type,
        "scheduled_at": when,
    }


def import_setup(raw, workspace_id="owner"):
    """Idempotent, authenticated storage only. Never create runnable app_posts."""
    if not workspace_id or len(workspace_id) > 128:
        raise ValueError("Invalid workspace")
    clean = validate(raw)
    with connect() as c:
        cursor = c.execute("""
          INSERT OR IGNORE INTO imported_guest_setups
          (workspace_id,setup_key,payload_json,imported_at)
          VALUES(?,?,?,?)
        """, (workspace_id, clean["setup_key"],
              json.dumps(clean, ensure_ascii=False), int(time.time())))
    return cursor.rowcount == 1


def list_setups(workspace_id="owner"):
    with connect() as c:
        rows = c.execute("""
            SELECT id,setup_key,payload_json,imported_at
            FROM imported_guest_setups WHERE workspace_id=?
            ORDER BY imported_at DESC,id DESC
        """, (workspace_id,)).fetchall()
    items = []
    for row in rows:
        data = json.loads(row["payload_json"])
        items.append({"id": row["id"], "imported_at": row["imported_at"],
                      **data})
    return items


def delete_setup(setup_id, workspace_id="owner"):
    with connect() as c:
        return c.execute(
            "DELETE FROM imported_guest_setups WHERE id=? AND workspace_id=?",
            (setup_id,workspace_id),
        ).rowcount == 1
