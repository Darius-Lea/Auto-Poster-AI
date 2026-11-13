"""Server-enforced customer Basic limits, independent of editable UI controls.

Basic permits one connected posting account per platform and one attempted
publication per platform per UTC calendar day. Different platforms may publish
in the same batch. Drafts do not consume publication quota. The legacy owner
workspace is deliberately exempt so existing live publishing is preserved.

Reservations are durable and never automatically released after an uncertain
network outcome. A repeating series creates a new post row for each occurrence;
it must not reuse an old delivery identity to avoid the daily quota.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import sqlite3

_SUPPORTED = {"facebook", "facebook_page", "instagram", "tiktok", "youtube",
              "linkedin", "pinterest", "threads", "x", "twitter", "website", "draft"}
_ACTIVE = ("scheduled", "sending", "published", "needs_review", "quota_held")
_HIGHER = {"creator", "business", "agency"}


class PlanLimitError(ValueError):
    status = 409


def _workspace(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise PlanLimitError("Choose a valid account workspace.")
    return value


def _connect():
    connection = sqlite3.connect(os.getenv("AUTOPOST_WEB_DB", "autopost_web.db"),
                                 timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def _init(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS plan_delivery_reservations(
        workspace_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        utc_day TEXT NOT NULL,
        post_id INTEGER NOT NULL,
        page_id TEXT,
        reserved_at INTEGER NOT NULL,
        PRIMARY KEY(workspace_id,platform,utc_day),
        UNIQUE(workspace_id,post_id)
    )""")


def init():
    with _connect() as connection:
        _init(connection)


def limits(workspace_id):
    workspace_id = _workspace(workspace_id)
    if workspace_id == "owner":
        return {"plan": "owner", "basic": False,
                "page_limit_per_platform": None, "daily_limit_per_platform": None,
                "quota_timezone": "UTC"}
    import app_control
    try:
        details = app_control.subscription_details(workspace_id=workspace_id)
    except sqlite3.OperationalError:
        details = {}
    plan = str(details.get("plan") or "basic").lower()
    # Only a verified, active higher-tier subscription can lift Basic limits.
    if plan not in _HIGHER or details.get("active") is not True:
        plan = "basic"
    basic = plan == "basic"
    return {"plan": plan, "basic": basic,
            "page_limit_per_platform": 1 if basic else None,
            "daily_limit_per_platform": 1 if basic else None,
            "quota_timezone": "UTC"}


def _platform(platform):
    if not isinstance(platform, str) or platform.lower().strip() not in _SUPPORTED:
        raise PlanLimitError("Choose a supported publishing platform.")
    platform = platform.lower().strip()
    return {"facebook_page": "facebook", "twitter": "x"}.get(platform, platform)


def _targets(targets):
    if not isinstance(targets, (list, tuple)) or not targets:
        raise PlanLimitError("Choose at least one destination.")
    parsed = []
    for target in targets:
        if not isinstance(target, (tuple, list)) or len(target) != 3:
            raise PlanLimitError("Choose valid publishing destinations.")
        platform, website, page_id = target
        normalized = _platform(platform)
        if page_id is not None and (not isinstance(page_id, str) or not page_id or len(page_id) > 128):
            raise PlanLimitError("Choose a valid posting Page.")
        if website is not None and not isinstance(website, str):
            raise PlanLimitError("Choose a valid website destination.")
        parsed.append((normalized, website, page_id))
    return parsed


def _connected_page_ids(connection, workspace_id):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='connected_facebook_pages'").fetchone():
        raise PlanLimitError("Account connections are not ready. Please try again after setup.")
    return {row[0] for row in connection.execute(
        "SELECT page_id FROM connected_facebook_pages WHERE workspace_id=?", (workspace_id,))}


def validate_pages(workspace_id, page_ids):
    """Call before activating Facebook Page connections (pending choices may be many)."""
    if not limits(workspace_id)["basic"]:
        return True
    if (not isinstance(page_ids, (list, tuple)) or
            any(not isinstance(page, str) or not page or len(page) > 128 for page in page_ids)):
        raise PlanLimitError("Choose valid Facebook Pages.")
    with _connect() as connection:
        combined = _connected_page_ids(connection, workspace_id) | set(page_ids)
    if len(combined) > 1:
        raise PlanLimitError("Basic includes one posting Page per platform. Disconnect the current Facebook Page before choosing another, or choose a plan with more Pages.")
    return True


def validate_destinations(workspace_id, targets):
    if not limits(workspace_id)["basic"]:
        return True
    parsed = _targets(targets)
    accounts = {}
    counts = {}
    for platform, website, page_id in parsed:
        if platform == "draft":
            continue
        counts[platform] = counts.get(platform, 0) + 1
        # An unconnected Facebook planning placeholder does not claim a new Page.
        identity = page_id or website or "__planned_account__"
        accounts.setdefault(platform, set()).add(identity)
    if any(len(pages) > 1 for pages in accounts.values()) or any(n > 1 for n in counts.values()):
        raise PlanLimitError("Basic can publish to one Page or account per platform. You may choose several different platforms together.")
    pages = [page_id for platform, _, page_id in parsed if platform == "facebook" and page_id]
    if pages:
        validate_pages(workspace_id, pages)
    return True


def validate_cadence(workspace_id, page_ids, cadence_seconds):
    if not limits(workspace_id)["basic"]:
        return True
    if (isinstance(cadence_seconds, bool) or not isinstance(cadence_seconds, (int, float))
            or not math.isfinite(cadence_seconds) or cadence_seconds < 86400):
        raise PlanLimitError("Basic allows one post per platform per day. Choose daily or weekly publishing, or change your plan for more frequent posts.")
    validate_pages(workspace_id, page_ids)
    return True


def _date(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            result = datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, ValueError, OSError):
            raise PlanLimitError("Choose a valid publishing date.") from None
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PlanLimitError("Choose a valid publishing date.") from None
    else:
        raise PlanLimitError("Choose a valid publishing date.")
    if result.tzinfo is None or result.utcoffset() is None:
        raise PlanLimitError("Publishing dates must include a timezone.")
    return result.astimezone(timezone.utc)


def _post_schema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(app_posts)")}
    if not {"id", "workspace_id", "platform", "due_at", "status", "page_id"} <= columns:
        raise PlanLimitError("Customer publishing storage is not ready. Please try again after setup.")


def _excluded(values):
    if values is None:
        return set()
    if not isinstance(values, (list, tuple, set)) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values
    ):
        raise PlanLimitError("Choose valid scheduled posts.")
    return set(values)


def check_schedule(workspace_id, targets, due_at, exclude_post_ids=None):
    """Reject conflicting active schedules; reserve_delivery is the final atomic gate."""
    if not limits(workspace_id)["basic"]:
        return True
    validate_destinations(workspace_id, targets)
    platforms = {p for p, _, _ in _targets(targets) if p != "draft"}
    if not platforms:
        return True
    excluded = _excluded(exclude_post_ids)
    day = _date(due_at).date().isoformat()
    with _connect() as connection:
        _init(connection)
        _post_schema(connection)
        rows = connection.execute("SELECT id,platform,status FROM app_posts WHERE workspace_id=? AND date(due_at)=?",
                                  (workspace_id, day)).fetchall()
        reserved = connection.execute("SELECT post_id,platform FROM plan_delivery_reservations WHERE workspace_id=? AND utc_day=?",
                                      (workspace_id, day)).fetchall()
    for row in rows:
        if row["id"] not in excluded and row["status"] in _ACTIVE and _platform(row["platform"]) in platforms:
            raise PlanLimitError("Basic already has a post for this platform on this date (UTC). Choose another day or change your plan.")
    for row in reserved:
        if row["post_id"] not in excluded and row["platform"] in platforms:
            raise PlanLimitError("Today's publishing allowance for this platform is already used or awaiting confirmation (UTC).")
    return True


def reserve_delivery(workspace_id, post_id, platform, page_id=None, now=None):
    """Atomically claim a Basic delivery allowance immediately before publication.

    Same-post retries preserve their original reservation; callers still must
    enforce their sending/needs_review state machine to prevent duplicate sends.
    A failure or uncertain response never frees the reservation automatically.
    """
    entitlement = limits(workspace_id)
    normalized = _platform(platform)
    if not entitlement["basic"] or normalized == "draft":
        return {"allowed": True, "exempt": True, "reserved": False, "already_reserved": False}
    if isinstance(post_id, bool) or not isinstance(post_id, int) or post_id < 1:
        raise PlanLimitError("Choose a valid post for publication.")
    moment = _date(now)
    day = moment.date().isoformat()
    with _connect() as connection:
        _init(connection)
        _post_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            post = connection.execute("SELECT * FROM app_posts WHERE id=? AND workspace_id=?", (post_id, workspace_id)).fetchone()
            if not post or _platform(post["platform"]) != normalized or post["page_id"] != page_id:
                raise PlanLimitError("This post does not belong to the selected account or Page.")
            if normalized == "facebook" and page_id:
                connected = _connected_page_ids(connection, workspace_id)
                if connected != {page_id}:
                    raise PlanLimitError("Basic requires one connected Facebook posting Page. Review your Page connections before publishing.")
            previous = connection.execute("SELECT * FROM plan_delivery_reservations WHERE workspace_id=? AND post_id=?", (workspace_id, post_id)).fetchone()
            if previous:
                if previous["platform"] != normalized or previous["page_id"] != page_id:
                    raise PlanLimitError("This delivery identity is already assigned to another destination.")
                connection.commit()
                return {"allowed": True, "exempt": False, "reserved": True,
                        "already_reserved": True, "platform": normalized, "day": previous["utc_day"]}
            spent = connection.execute("SELECT post_id FROM plan_delivery_reservations WHERE workspace_id=? AND platform=? AND utc_day=?", (workspace_id, normalized, day)).fetchone()
            if spent:
                raise PlanLimitError("Basic allows one post per platform per day (UTC). This platform's allowance is already used or awaiting confirmation.")
            connection.execute("INSERT INTO plan_delivery_reservations VALUES(?,?,?,?,?,?)",
                               (workspace_id, normalized, day, post_id, page_id, int(moment.timestamp())))
            connection.commit()
            return {"allowed": True, "exempt": False, "reserved": True,
                    "already_reserved": False, "platform": normalized, "day": day}
        except Exception:
            connection.rollback()
            raise
