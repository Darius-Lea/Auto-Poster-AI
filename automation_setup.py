"""Pre-payment setup plans for Auto Poster AI.

Plans are intentional drafts. Saving a plan MUST NOT schedule a worker, call
a social API, fetch a website, or change paid publishing entitlements.
"""
from __future__ import annotations

import os
import sqlite3
import time
from urllib.parse import urlparse

WORKSPACE = "owner"
KINDS = {"manual": "My own posts", "ai": "AI content (planned)", "website": "Approved website/feed (planned)"}
CADENCES = {"daily", "twice_daily", "three_daily", "weekly"}
REVIEWS = {"approve_first", "autopilot_when_supported"}
MAX_PLANS = 30


def connect():
    c = sqlite3.connect(os.getenv("AUTOPOST_WEB_DB", "autopost_web.db"), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    return c


def init():
    with connect() as c:
        c.execute("""
          CREATE TABLE IF NOT EXISTS automation_setup_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            topic TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            destination TEXT NOT NULL,
            cadence TEXT NOT NULL,
            review_mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'setup_only',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
          )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS automation_setup_owner ON automation_setup_plans(workspace_id)")


def validate(values):
    name = str(values.get("name", "")).strip()
    topic = str(values.get("topic", "")).strip()
    kind = str(values.get("kind", "")).strip()
    dest = str(values.get("destination", "")).strip()
    cadence = str(values.get("cadence", "")).strip()
    review_mode = str(values.get("review_mode", "")).strip()
    url = str(values.get("source_url", "")).strip()
    if not 2 <= len(name) <= 80:
        raise ValueError("Give your automation a name (2–80 characters)")
    if not 2 <= len(topic) <= 300:
        raise ValueError("Describe the content you want to publish (2–300 characters)")
    if kind not in KINDS or cadence not in CADENCES or review_mode not in REVIEWS:
        raise ValueError("Choose a valid content source, frequency and approval mode")
    # The no-connection-yet option is intentional. It cannot start publication.
    if dest not in {"facebook_later", "draft_later"} and not dest.startswith("page:"):
        raise ValueError("Choose an intended destination")
    if dest.startswith("page:"):
        import meta_accounts
        chosen = dest.split(":", 1)[1]
        if chosen not in {p["page_id"] for p in meta_accounts.list_pages(WORKSPACE)
                          if p["can_publish"]}:
            raise ValueError("That Page is not connected to this workspace")
    if kind == "website":
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or len(url) > 1500 or parsed.port not in (None, 443)
                or parsed.hostname.lower() in {"localhost"}
                or parsed.hostname.lower().endswith((".local", ".internal"))):
            raise ValueError("Enter a permitted public HTTPS website/feed URL")
    else:
        url = ""
    return {"name": name, "topic": topic, "kind": kind, "source_url": url,
            "destination": dest, "cadence": cadence, "review_mode": review_mode}


def save(values, workspace_id=WORKSPACE, plan_id=None):
    if not workspace_id or len(workspace_id) > 128:
        raise ValueError("Unknown workspace")
    clean = validate(values)
    now = int(time.time())
    with connect() as c:
        if plan_id is None:
            n = c.execute("SELECT COUNT(*) FROM automation_setup_plans WHERE workspace_id=?",
                          (workspace_id,)).fetchone()[0]
            if n >= MAX_PLANS:
                raise ValueError("Your setup plan limit has been reached")
            cur = c.execute("""
                INSERT INTO automation_setup_plans
                (workspace_id,name,topic,kind,source_url,destination,cadence,review_mode,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (workspace_id, *(clean[k] for k in (
                  "name","topic","kind","source_url","destination","cadence","review_mode")), now,now))
            return cur.lastrowid
        cur = c.execute("""
            UPDATE automation_setup_plans SET name=?,topic=?,kind=?,source_url=?,
                   destination=?,cadence=?,review_mode=?,updated_at=?
            WHERE id=? AND workspace_id=?
        """, (*(clean[k] for k in (
               "name","topic","kind","source_url","destination","cadence","review_mode")),now,
              plan_id,workspace_id))
        if cur.rowcount != 1:
            raise ValueError("Automation plan not found")
        return plan_id


def list_plans(workspace_id=WORKSPACE):
    with connect() as c:
        return [dict(row) for row in c.execute("""
            SELECT * FROM automation_setup_plans WHERE workspace_id=?
            ORDER BY updated_at DESC, id DESC
        """, (workspace_id,))]


def delete(plan_id, workspace_id=WORKSPACE):
    with connect() as c:
        return c.execute(
            "DELETE FROM automation_setup_plans WHERE id=? AND workspace_id=?",
            (plan_id,workspace_id),
        ).rowcount == 1
