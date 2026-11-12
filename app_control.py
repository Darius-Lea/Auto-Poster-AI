"""Single-owner commercial controls for the current Auto Poster AI starter.

No customer may self-activate billing via a form. Subscription status must come
from a signature-verified payment provider webhook. The mobile dashboard and
the GitHub Actions bridge consult this same server-side state.
"""
from __future__ import annotations

import os
import sqlite3
import time
from urllib.parse import urlparse

DATABASE = lambda: os.getenv("AUTOPOST_WEB_DB", "autopost_web.db")
WORKSPACE = "owner"
SOURCES = ("booking", "warrant", "case", "status")
ALLOWED_MODES = {
    "publish": "booking",
    "publish-and-check": "booking",
    "publish-wanted": "warrant",
    "publish-case": "case",
    "check-status": "status",
}
INTERVAL_KEYS = {
    "booking": "booking_interval_minutes",
    "warrant": "warrant_interval_minutes",
    "case": "case_interval_minutes",
    "status": "status_interval_minutes",
}
FLAG_KEYS = {
    "booking": "booking_enabled", "warrant": "warrant_enabled",
    "case": "case_enabled", "status": "status_enabled",
}
DEFAULTS = {
    "enabled": 0, "booking_enabled": 1, "warrant_enabled": 0,
    "case_enabled": 0, "status_enabled": 1,
    "booking_interval_minutes": 60, "warrant_interval_minutes": 1440,
    "case_interval_minutes": 1440, "status_interval_minutes": 180,
    "max_posts_per_run": 1, "batch_size": 5, "release_enabled": 0,
}


def connect():
    conn = sqlite3.connect(DATABASE(), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
          workspace_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          customer_id TEXT,
          subscription_id TEXT NOT NULL,
          status TEXT NOT NULL,
          plan TEXT NOT NULL,
          current_period_end INTEGER NOT NULL,
          event_created INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publishing_controls (
          workspace_id TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 0,
          booking_enabled INTEGER NOT NULL DEFAULT 1,
          warrant_enabled INTEGER NOT NULL DEFAULT 0,
          case_enabled INTEGER NOT NULL DEFAULT 0,
          status_enabled INTEGER NOT NULL DEFAULT 1,
          booking_interval_minutes INTEGER NOT NULL DEFAULT 60,
          warrant_interval_minutes INTEGER NOT NULL DEFAULT 1440,
          case_interval_minutes INTEGER NOT NULL DEFAULT 1440,
          status_interval_minutes INTEGER NOT NULL DEFAULT 180,
          max_posts_per_run INTEGER NOT NULL DEFAULT 1,
          batch_size INTEGER NOT NULL DEFAULT 5,
          release_enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS publishing_claims (
          workspace_id TEXT NOT NULL,
          source TEXT NOT NULL,
          last_claimed_at INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (workspace_id, source)
        );
        """)
        c.execute("INSERT OR IGNORE INTO publishing_controls(workspace_id) VALUES(?)", (WORKSPACE,))


def require_payment() -> bool:
    # The production Render blueprint sets this true. The original single-owner
    # automation remains unchanged until the app is explicitly connected.
    return os.getenv("AUTOPOST_REQUIRE_PAYMENT", "false").lower() == "true"


def subscription_details(now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    with connect() as c:
        row = c.execute(
            "SELECT * FROM subscriptions WHERE workspace_id=?", (WORKSPACE,)
        ).fetchone()
    if not row:
        return {"active": False, "plan": "basic", "status": "not_subscribed",
                "current_period_end": None}
    return {"active": row["status"] == "active" and row["current_period_end"] > now,
            "plan": row["plan"], "status": row["status"],
            "current_period_end": row["current_period_end"]}


def allowed(now: int | None = None) -> bool:
    if not require_payment():
        return True
    return subscription_details(now)["active"]


def record_subscription_event(event: dict) -> bool:
    """Persist a *verified* Stripe subscription event, newer revisions only.

    Caller MUST authenticate the event with Stripe webhook signature validation.
    Never call this function with a browser-submitted subscription object.
    """
    if event.get("type") not in {
        "customer.subscription.created", "customer.subscription.updated",
        "customer.subscription.deleted"
    }:
        return False
    obj = event.get("data", {}).get("object", {})
    if not isinstance(obj, dict):
        return False
    if obj.get("metadata", {}).get("workspace_id") != WORKSPACE:
        return False
    subscription_id = obj.get("id")
    if not subscription_id or not str(subscription_id).startswith("sub_"):
        return False
    created = int(event.get("created", 0))
    period_end = int(obj.get("current_period_end", 0) or 0)
    status = str(obj.get("status", "unknown"))
    with connect() as c:
        # event_created prevents an older webhook delivery from reactivating
        # an expired or canceled subscription.
        c.execute("""
          INSERT INTO subscriptions (
            workspace_id,provider,customer_id,subscription_id,status,plan,
            current_period_end,event_created,updated_at
          ) VALUES (?,'stripe',?,?,?,?,?,?,?)
          ON CONFLICT(workspace_id) DO UPDATE SET
            provider=excluded.provider,customer_id=excluded.customer_id,
            subscription_id=excluded.subscription_id,status=excluded.status,
            plan=excluded.plan,current_period_end=excluded.current_period_end,
            event_created=excluded.event_created,updated_at=excluded.updated_at
          WHERE excluded.event_created >= subscriptions.event_created
            AND excluded.subscription_id = subscriptions.subscription_id
        """, (WORKSPACE, obj.get("customer"), subscription_id, status,
              "basic", period_end, created, int(time.time())))
    return True


def get_settings() -> dict:
    with connect() as c:
        r = c.execute(
            "SELECT * FROM publishing_controls WHERE workspace_id=?", (WORKSPACE,)
        ).fetchone()
    return dict(r) if r else dict(DEFAULTS)


def update_settings(values: dict) -> dict:
    """Only documented keys may be changed; subscription state is not editable."""
    validated = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError("Unknown setting " + key)
        try:
            n = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid value for " + key) from exc
        if key.endswith("_enabled") or key in ("enabled", "release_enabled"):
            if n not in (0, 1):
                raise ValueError(key + " must be on or off")
        elif key == "max_posts_per_run":
            if not 1 <= n <= 10:
                raise ValueError("1–10 posts per scheduled run")
        elif key == "batch_size":
            if not 0 <= n <= 10:
                raise ValueError("0–10 photos per batch")
        elif key.endswith("_interval_minutes"):
            if not 15 <= n <= 10080:
                raise ValueError("Interval must be 15 to 10080 minutes")
        validated[key] = n
    if validated:
        sql = ", ".join(k + "=?" for k in validated)
        with connect() as c:
            c.execute("UPDATE publishing_controls SET " + sql
                      + " WHERE workspace_id=?",
                      (*validated.values(), WORKSPACE))
    return get_settings()


def claim_run(requested_mode: str, now: int | None = None) -> dict:
    """Atomic remote schedule gate. A failure to contact this gate fails closed.

    Claim before running the old pipeline to avoid overlap. An interrupted
    claim may skip one interval rather than duplicating a sensitive post.
    """
    if requested_mode not in ALLOWED_MODES:
        return {"run": False, "reason": "Unsupported publishing mode"}
    if not allowed(now):
        return {"run": False, "reason": "Paid subscription required"}
    now = int(time.time()) if now is None else now
    source = ALLOWED_MODES[requested_mode]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        settings_row = c.execute(
            "SELECT * FROM publishing_controls WHERE workspace_id=?", (WORKSPACE,)
        ).fetchone()
        if not settings_row:
            return {"run": False, "reason": "Control app not initialized"}
        s = dict(settings_row)
        if not s["enabled"]:
            return {"run": False, "reason": "Autopilot paused in mobile app"}
        if not s[FLAG_KEYS[source]]:
            if requested_mode == "publish-and-check" and s["status_enabled"]:
                # Keep custody checks running even when new booking posts pause.
                requested_mode = "check-status"
                source = "status"
            else:
                return {"run": False, "reason": source + " posting disabled"}
        if requested_mode == "publish-and-check" and not s["status_enabled"]:
            # Do not silently continue custody/release checks after they were disabled.
            requested_mode = "publish"
        interval = s[INTERVAL_KEYS[source]] * 60
        previous = c.execute(
            "SELECT last_claimed_at FROM publishing_claims "
            "WHERE workspace_id=? AND source=?", (WORKSPACE, source)
        ).fetchone()
        if previous and now - previous["last_claimed_at"] < interval:
            return {"run": False, "reason": "Waiting for next configured interval"}
        c.execute("""
          INSERT INTO publishing_claims(workspace_id,source,last_claimed_at)
          VALUES(?,?,?)
          ON CONFLICT(workspace_id,source)
          DO UPDATE SET last_claimed_at=excluded.last_claimed_at
        """, (WORKSPACE, source, now))
    return {"run": True, "mode": requested_mode,
            "limit": s["max_posts_per_run"], "batch_size": s["batch_size"],
            "release_enabled": bool(s["release_enabled"]), "reason": "Authorized"}
