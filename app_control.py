"""Single-owner commercial controls for the current Auto Poster AI starter.

No customer may self-activate billing via a form. Subscription status must come
from a signature-verified payment provider webhook. The mobile dashboard and
the GitHub Actions bridge consult this same server-side state.
"""
from __future__ import annotations

import os
import secrets
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
    "catchup": "booking",
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


def init(workspace_id=WORKSPACE):
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
        c.executescript("""
        CREATE TABLE IF NOT EXISTS publishing_pauses (
          workspace_id TEXT PRIMARY KEY,paused INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS billing_checkout_attempts (
          workspace_id TEXT PRIMARY KEY,plan TEXT NOT NULL,price_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,session_id TEXT,session_url TEXT);
        """)
        c.execute("INSERT OR IGNORE INTO publishing_controls(workspace_id) VALUES(?)", (workspace_id,))


def require_payment() -> bool:
    # The production Render blueprint sets this true. The original single-owner
    # automation remains unchanged until the app is explicitly connected.
    return os.getenv("AUTOPOST_REQUIRE_PAYMENT", "false").lower() == "true"


def subscription_details(now: int | None = None, workspace_id=WORKSPACE) -> dict:
    now = int(time.time()) if now is None else now
    with connect() as c:
        row = c.execute(
            "SELECT * FROM subscriptions WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
    if not row:
        return {"active": False, "plan": "basic", "status": "not_subscribed",
                "current_period_end": None}
    return {"active": row["status"] == "active" and row["current_period_end"] > now,
            "plan": row["plan"], "status": row["status"],
            "current_period_end": row["current_period_end"]}


def allowed(now: int | None = None, workspace_id=WORKSPACE) -> bool:
    if not require_payment():
        return True
    return subscription_details(now, workspace_id)["active"]


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
    metadata = obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    workspace_id = metadata.get("workspace_id")
    if not isinstance(workspace_id, str) or len(workspace_id)>128:
        return False
    if workspace_id != WORKSPACE:
        import customer_accounts
        try:
            if not customer_accounts.workspace_exists(workspace_id):
                return False
        except sqlite3.OperationalError:
            return False
    subscription_id = obj.get("id")
    if not subscription_id or not str(subscription_id).startswith("sub_"):
        return False
    try:
        created = int(event.get("created", 0))
    except (ValueError,TypeError,OverflowError):
        return False
    # Stripe 2025-03-31.basil and newer moved billing periods to items.
    # Fail closed rather than guessing if the paid period is unavailable.
    container=obj.get("items") or {}
    if not isinstance(container,dict):return False
    items=container.get("data",[]) or []
    if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):return False
    try:
        ends = [int(item.get("current_period_end") or 0) for item in items]
        period_end = min(ends) if ends else int(obj.get("current_period_end", 0) or 0)
    except (ValueError,TypeError,OverflowError):
        return False
    status = str(obj.get("status", "unknown"))
    configured = {entry["price_id"]: name for name,entry in billing_plans().items() if entry["configured"]}
    item_prices = [item.get("price", {}) for item in items if isinstance(item,dict)]
    price_ids = [price.get("id") if isinstance(price,dict) else price for price in item_prices]
    recognized = {configured[price_id] for price_id in price_ids if price_id in configured}
    # Only a service-configured price can grant a higher tier. Client-selected
    # metadata, an unrecognized price and legacy fixture events remain Basic.
    plan = next(iter(recognized)) if len(recognized)==1 and len(price_ids)==1 else "basic"
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
          WHERE (excluded.event_created > subscriptions.event_created
            OR (excluded.event_created = subscriptions.event_created
                AND CASE excluded.status WHEN 'canceled' THEN 4 WHEN 'incomplete_expired' THEN 4 WHEN 'unpaid' THEN 3 WHEN 'past_due' THEN 2 ELSE 1 END
                  > CASE subscriptions.status WHEN 'canceled' THEN 4 WHEN 'incomplete_expired' THEN 4 WHEN 'unpaid' THEN 3 WHEN 'past_due' THEN 2 ELSE 1 END))
            AND (excluded.subscription_id = subscriptions.subscription_id
                 OR subscriptions.status != 'active'
                 OR subscriptions.current_period_end <= excluded.updated_at)
        """, (workspace_id, obj.get("customer"), subscription_id, status,
              plan, period_end, created, int(time.time())))
    return True


def get_settings(workspace_id=WORKSPACE) -> dict:
    with connect() as c:
        r = c.execute(
            "SELECT * FROM publishing_controls WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
    return dict(r) if r else dict(DEFAULTS)


def update_settings(values: dict, workspace_id=WORKSPACE) -> dict:
    """Only documented keys may be changed; subscription state is not editable."""
    init(workspace_id)
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
                      (*validated.values(), workspace_id))
    return get_settings(workspace_id)


def claim_run(requested_mode: str, now: int | None = None, workspace_id=WORKSPACE) -> dict:
    """Atomic remote schedule gate. A failure to contact this gate fails closed.

    Claim before running the old pipeline to avoid overlap. An interrupted
    claim may skip one interval rather than duplicating a sensitive post.
    """
    if requested_mode not in ALLOWED_MODES:
        return {"run": False, "reason": "Unsupported publishing mode"}
    if not allowed(now, workspace_id):
        return {"run": False, "reason": "Paid subscription required"}
    if publishing_paused(workspace_id):
        return {"run": False, "reason": "Publishing paused in this workspace"}
    now = int(time.time()) if now is None else now
    source = ALLOWED_MODES[requested_mode]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        settings_row = c.execute(
            "SELECT * FROM publishing_controls WHERE workspace_id=?", (workspace_id,)
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
        if requested_mode == "catchup":
            return {"run": True, "mode": "catchup",
                    "limit": s["max_posts_per_run"], "batch_size": s["batch_size"],
                    "release_enabled": bool(s["release_enabled"]), "reason": "Authorized catch-up"}
        interval = s[INTERVAL_KEYS[source]] * 60
        previous = c.execute(
            "SELECT last_claimed_at FROM publishing_claims "
            "WHERE workspace_id=? AND source=?", (workspace_id, source)
        ).fetchone()
        if previous and now - previous["last_claimed_at"] < interval:
            return {"run": False, "reason": "Waiting for next configured interval"}
        c.execute("""
          INSERT INTO publishing_claims(workspace_id,source,last_claimed_at)
          VALUES(?,?,?)
          ON CONFLICT(workspace_id,source)
          DO UPDATE SET last_claimed_at=excluded.last_claimed_at
        """, (workspace_id, source, now))
    return {"run": True, "mode": requested_mode,
            "limit": s["max_posts_per_run"], "batch_size": s["batch_size"],
            "release_enabled": bool(s["release_enabled"]), "reason": "Authorized"}


# This is deliberately separate from the legacy source-control `enabled` flag:
# no existing owner schedule is silently paused by the migration.
def publishing_paused(workspace_id=WORKSPACE):
    with connect() as c:
        try:
            row=c.execute("SELECT paused FROM publishing_pauses WHERE workspace_id=?",(workspace_id,)).fetchone()
        except sqlite3.OperationalError:
            return False
    return bool(row and row["paused"])


def set_publishing_paused(workspace_id,paused):
    if not isinstance(workspace_id,str) or not workspace_id or len(workspace_id)>128 or type(paused) is not bool:
        raise ValueError("Choose whether to pause or resume publishing.")
    init(workspace_id)
    with connect() as c:
        c.execute("INSERT INTO publishing_pauses VALUES(?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET paused=excluded.paused,updated_at=excluded.updated_at",(workspace_id,int(paused),int(time.time())))
    return paused


def billing_plans():
    labels={"basic":"Basic","creator":"Creator","business":"Business","agency":"Agency"}
    result={name:{"label":label,"configured":False,"price_id":os.getenv("STRIPE_"+name.upper()+"_PRICE_ID","").strip()} for name,label in labels.items()}
    prices=[entry['price_id'] for entry in result.values() if entry['price_id']]
    for entry in result.values():
        price=entry['price_id']
        entry['configured']=bool(price.startswith('price_') and len(price)<=200 and not any(c.isspace() for c in price) and prices.count(price)==1)
    return result


def public_origin():
    raw=os.getenv("AUTOPOST_PUBLIC_URL","").strip()
    try:
        value=urlparse(raw)
        if any(c.isspace() for c in raw) or value.scheme!="https" or not value.hostname or value.username or value.password or value.query or value.fragment or value.path not in ("","/") or not value.port is None and not 1<=value.port<=65535:
            raise ValueError
    except (ValueError,TypeError):
        raise ValueError("Subscription setup is not ready. Please try again later.") from None
    return raw.rstrip("/")


def checkout_attempt(workspace_id,plan,now=None):
    moment=int(time.time()) if now is None else int(now)
    plans=billing_plans()
    if plan not in plans or not plans[plan]["configured"]:
        raise ValueError("That plan is not available for activation yet.")
    price=plans[plan]["price_id"]
    if not price.startswith("price_") or len(price)>200 or any(x.isspace() for x in price):
        raise ValueError("That plan is not available for activation yet.")
    init(workspace_id)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing=c.execute("SELECT * FROM billing_checkout_attempts WHERE workspace_id=?",(workspace_id,)).fetchone()
        if existing and existing["expires_at"]>moment:
            if existing["plan"]!=plan or existing["price_id"]!=price:
                raise ValueError("A checkout is already open. Finish or let it expire before choosing another plan.")
            return dict(existing)
        key="autopost-checkout-"+secrets.token_hex(24)
        c.execute("INSERT OR REPLACE INTO billing_checkout_attempts VALUES(?,?,?,?,?,?,NULL,NULL)",(workspace_id,plan,price,key,moment,moment+1860))
        return dict(c.execute("SELECT * FROM billing_checkout_attempts WHERE workspace_id=?",(workspace_id,)).fetchone())


def save_checkout(workspace_id,idempotency_key,session_id,session_url):
    u=urlparse(session_url or "")
    if u.scheme!="https" or u.hostname!="checkout.stripe.com" or u.username or u.password:
        raise ValueError("The payment service did not return a valid checkout. Please try again.")
    with connect() as c:
        c.execute("UPDATE billing_checkout_attempts SET session_id=?,session_url=? WHERE workspace_id=? AND idempotency_key=?",(session_id,session_url,workspace_id,idempotency_key))
