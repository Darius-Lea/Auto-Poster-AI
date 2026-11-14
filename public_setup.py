"""Validate and import an anonymous, browser-created setup after owner authentication.

The public wizard never writes to this database. Imported setups are inert
configuration records. Neither payment nor import can start automation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
        c.execute("""
          CREATE TABLE IF NOT EXISTS prepared_guest_setup_posts(
            workspace_id TEXT NOT NULL,
            setup_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            configuration_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(workspace_id,setup_id)
          )
        """)
        c.execute("""
          CREATE TABLE IF NOT EXISTS prepared_guest_setup_post_items(
            workspace_id TEXT NOT NULL,
            setup_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            PRIMARY KEY(workspace_id,setup_id,post_id)
          )
        """)
        c.execute("""INSERT OR IGNORE INTO prepared_guest_setup_post_items
                     SELECT workspace_id,setup_id,post_id FROM prepared_guest_setup_posts""")


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid setup data")
    key = raw.get("setup_key")
    if not isinstance(key, str) or not HEX_ID.fullmatch(key):
        raise ValueError("Your setup has an invalid identifier")
    platforms = raw.get("platforms")
    if (not isinstance(platforms, list) or not platforms or len(platforms) > 8
            or any(not isinstance(p, str) or p not in PLATFORMS for p in platforms)
            or len(set(platforms)) != len(platforms)):
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
            SELECT s.id,s.setup_key,s.payload_json,s.imported_at,p.post_id AS prepared_post_id
            FROM imported_guest_setups s LEFT JOIN prepared_guest_setup_posts p
            ON p.workspace_id=s.workspace_id AND p.setup_id=s.id
            WHERE s.workspace_id=? ORDER BY s.imported_at DESC,s.id DESC
        """, (workspace_id,)).fetchall()
    items = []
    for row in rows:
        data = json.loads(row["payload_json"])
        items.append({"id": row["id"], "imported_at": row["imported_at"],
                      "prepared_post_id": row["prepared_post_id"], **data})
    with connect() as c:
        for item in items:
            mapped = c.execute(
                "SELECT post_id FROM prepared_guest_setup_post_items WHERE workspace_id=? AND setup_id=? ORDER BY post_id",
                (workspace_id, item["id"]),
            ).fetchall()
            item["prepared_post_ids"] = [p["post_id"] for p in mapped]
    return items


def delete_setup(setup_id, workspace_id="owner"):
    with connect() as c:
        return c.execute(
            "DELETE FROM imported_guest_setups WHERE id=? AND workspace_id=?",
            (setup_id,workspace_id),
        ).rowcount == 1


def prepare_posts(setup_id, values, workspace_id="owner", now=None):
    """Create inactive posts for explicitly selected destinations, atomically.

    The caller derives workspace_id from an authenticated server session. This
    never subscribes, publishes or activates external delivery. Retry returns
    the same post IDs. Explicit revisions can update draft_ready/setup_only
    posts in place, including converting one draft to multiple selected Pages.
    Already activated work cannot be silently duplicated or rescheduled here.
    """
    if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 128:
        raise ValueError("Invalid workspace")
    if not isinstance(values, dict):
        raise ValueError("Choose a destination and posting time")
    try:
        setup_id = int(setup_id)
    except (TypeError, ValueError):
        raise ValueError("Saved setup not found") from None
    if setup_id < 1:
        raise ValueError("Saved setup not found")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    now = now.astimezone(timezone.utc)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT payload_json FROM imported_guest_setups WHERE id=? AND workspace_id=?",
            (setup_id, workspace_id),
        ).fetchone()
        if not row:
            raise ValueError("Saved setup not found")
        clean = validate(json.loads(row["payload_json"]))
        if clean["source"] != "own_posts":
            raise ValueError("This content source needs its supported automation connection before preparation")
        frequency = values.get("frequency", clean["frequency"])
        if frequency not in {"once", "daily", "weekly"}:
            raise ValueError("Choose once, daily or weekly; this scheduler does not yet support that frequency")
        zone_name = values.get("time_zone", "UTC")
        try:
            ZoneInfo(zone_name)
        except (KeyError, ValueError, TypeError):
            raise ValueError("Choose a valid timezone") from None
        destination = values.get("destination")
        if destination == "draft":
            targets, status = [("draft", None)], "draft_ready"
        elif destination == "facebook_page":
            if "facebook" not in clean["platforms"]:
                raise ValueError("This setup does not include Facebook")
            import meta_accounts
            page_ids = values.get("page_ids")
            if page_ids is None:
                page_ids = [values.get("page_id")]
            connected = {p["page_id"] for p in meta_accounts.list_pages(workspace_id) if p["can_publish"]}
            if (not isinstance(page_ids, list) or not 1 <= len(page_ids) <= 12
                    or any(not isinstance(pid, str) or pid not in connected for pid in page_ids)
                    or len(set(page_ids)) != len(page_ids)):
                raise ValueError("Connect and select Facebook Pages belonging to this account")
            targets = [("facebook_page", pid) for pid in sorted(page_ids)]
            status = "setup_only"
        elif destination == "facebook":
            if workspace_id != "owner" or "facebook" not in clean["platforms"]:
                raise ValueError("Choose a Page connected to your own account")
            if not (os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")):
                raise ValueError("Connect a Facebook Page before preparing this post")
            targets, status = [("facebook", None)], "setup_only"
        else:
            raise ValueError("Choose a draft or supported connected Facebook Pages")
        due = None
        if destination != "draft":
            raw_due = values.get("due_at", clean["scheduled_at"])
            if not isinstance(raw_due, str) or len(raw_due) > 42:
                raise ValueError("Choose a future posting time")
            try:
                due = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                if due.tzinfo is None or abs(due.utcoffset().total_seconds()) > 14 * 3600:
                    raise ValueError
                due = due.astimezone(timezone.utc)
            except (ValueError, AttributeError, OverflowError):
                raise ValueError("Choose a posting time including its timezone") from None
        configuration = {
            "targets": targets, "frequency": frequency, "time_zone": zone_name,
            "due_at": due.isoformat(timespec="seconds") if due else None,
            "content": clean["message"],
        }
        fingerprint = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        post_columns = {r[1] for r in c.execute("PRAGMA table_info(app_posts)")}
        series_columns = {r[1] for r in c.execute("PRAGMA table_info(app_series)")}
        if workspace_id != "owner" and ("workspace_id" not in post_columns or "workspace_id" not in series_columns):
            raise ValueError("Customer workspace scheduling is not available on this server yet")
        prepared = c.execute(
            "SELECT post_id,configuration_hash FROM prepared_guest_setup_posts WHERE workspace_id=? AND setup_id=?",
            (workspace_id, setup_id),
        ).fetchone()
        existing = []
        if prepared:
            existing = c.execute(
                "SELECT p.id,p.platform,p.page_id,p.status,p.series_id FROM app_posts p "
                "JOIN prepared_guest_setup_post_items i ON i.post_id=p.id "
                "WHERE i.workspace_id=? AND i.setup_id=?" +
                (" AND p.workspace_id=?" if "workspace_id" in post_columns else "") + " ORDER BY p.id",
                (workspace_id, setup_id, workspace_id) if "workspace_id" in post_columns else (workspace_id, setup_id),
            ).fetchall()
            if not existing:
                raise ValueError("The prepared posts are no longer available")
            if prepared["configuration_hash"] == fingerprint:
                if {(post["platform"], post["page_id"]) for post in existing} != set(targets):
                    raise ValueError("One or more prepared posts are no longer available")
                return _prepared_result(existing, 0, False)
            if any(post["status"] not in {"draft_ready", "setup_only"} for post in existing):
                raise ValueError("This post has already been activated or ended; create a new reviewed setup")
        if due and due <= now:
            raise ValueError("That posting time has passed; choose a fresh future time")
        for post in existing:
            if post["series_id"]:
                c.execute(
                    "UPDATE app_series SET active=0 WHERE id=?" +
                    (" AND workspace_id=?" if "workspace_id" in series_columns else ""),
                    (post["series_id"], workspace_id) if "workspace_id" in series_columns else (post["series_id"],),
                )
        remaining = list(existing)
        posts, created_count = [], 0
        for platform, page_id in targets:
            matched = next((post for post in remaining
                            if post["platform"] == platform and post["page_id"] == page_id), None)
            # Reuse a pending draft/post when the user deliberately changes its
            # destination, preserving one reviewable record where possible.
            if matched is None:
                matched = next((post for post in remaining
                                if (post["platform"], post["page_id"]) not in set(targets)), None)
            if matched is not None:
                remaining.remove(matched)
            series_id = None
            if frequency != "once" and platform != "draft":
                names = ["content", "platform", "frequency", "time_zone", "page_id"]
                args = [clean["message"], platform, frequency, zone_name, page_id]
                if "workspace_id" in series_columns:
                    names.append("workspace_id"); args.append(workspace_id)
                cursor = c.execute(
                    "INSERT INTO app_series(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", args,
                )
                series_id = cursor.lastrowid
            names = ["content", "platform", "due_at", "status", "series_id", "frequency", "page_id"]
            args = [clean["message"], platform, (due or now).isoformat(timespec="seconds"), status,
                    series_id, frequency, page_id]
            if matched:
                where = " WHERE id=?" + (" AND workspace_id=?" if "workspace_id" in post_columns else "")
                identifiers = [matched["id"], workspace_id] if "workspace_id" in post_columns else [matched["id"]]
                c.execute("UPDATE app_posts SET " + ",".join(name + "=?" for name in names) + where, args + identifiers)
                post_id = matched["id"]
            else:
                if "workspace_id" in post_columns:
                    names.append("workspace_id"); args.append(workspace_id)
                cursor = c.execute(
                    "INSERT INTO app_posts(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", args,
                )
                post_id = cursor.lastrowid
                created_count += 1
            posts.append({"id": post_id, "status": status, "platform": platform, "page_id": page_id})
        for post in remaining:
            c.execute(
                "UPDATE app_posts SET status='cancelled' WHERE id=?" +
                (" AND workspace_id=?" if "workspace_id" in post_columns else ""),
                (post["id"], workspace_id) if "workspace_id" in post_columns else (post["id"],),
            )
        c.execute("DELETE FROM prepared_guest_setup_post_items WHERE workspace_id=? AND setup_id=?",
                  (workspace_id, setup_id))
        c.executemany("INSERT INTO prepared_guest_setup_post_items VALUES(?,?,?)",
                      [(workspace_id, setup_id, p["id"]) for p in posts])
        c.execute("""INSERT INTO prepared_guest_setup_posts VALUES(?,?,?,?,?)
                     ON CONFLICT(workspace_id,setup_id) DO UPDATE SET
                     post_id=excluded.post_id,configuration_hash=excluded.configuration_hash""",
                  (workspace_id, setup_id, posts[0]["id"], fingerprint, int(now.timestamp())))
        return _prepared_result(posts, created_count, bool(existing))


def _prepared_result(posts, created_count, updated):
    values = [dict(post) for post in posts]
    return {"post_id": values[0]["id"], "status": values[0]["status"],
            "post_ids": [p["id"] for p in values], "posts": values,
            "created": created_count > 0, "created_count": created_count, "updated": updated}
