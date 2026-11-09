"""Standalone RELEASED posts for people who leave the jail roster.

The custody monitor confirms a departure (two complete roster observations
with the person missing) and edits the original booking post. This module
adds the public announcement the Page owner asked for: one new Page post
per confirmed departure, with the person's photo and booking charges when
they are on file, or a text post when no photo is available.

A SQLite ledger gives every departure exactly one release post. Ambiguous
Facebook outcomes are held as uncertain and never retried automatically,
mirroring the delivery and group ledgers. Only departures confirmed within
the lookback window are announced, so enabling this flow never republishes
old departures in a burst.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from adapters._common import download_image
from core import grouped, watermark
from core.facebook import FacebookDeliveryUnknown, FacebookError

log = logging.getLogger("core.release_posts")

STATUSES = {"pending", "published", "uncertain", "failed"}
LOCAL_TIME = ZoneInfo("America/Indiana/Indianapolis")
DISCLAIMER = "All individuals are presumed innocent until proven guilty."
DEFAULT_LOOKBACK_HOURS = 48


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS release_posts (
            record_hash TEXT NOT NULL,
            destination TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'uncertain', 'failed')),
            post_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (record_hash, destination)
        )
    """)
    conn.commit()


def render_release_caption(person: dict, checked_at: datetime) -> str:
    """Render one departure announcement in the Page's magazine style."""
    if checked_at.utcoffset() is None:
        raise ValueError("The roster check time must be timezone-aware")
    name = " ".join(str(person.get("name") or "").split()) or "A former inmate"
    details = []
    booked = " ".join(str(person.get("booking_date") or "").split())
    if booked:
        details.append(f"Booked {booked}.")
    charge = " ".join(str(person.get("charge") or "").split())
    if charge:
        details.append(f"Charges at booking: {charge}.")
    local = checked_at.astimezone(LOCAL_TIME)
    sections = [
        "RELEASED — ALLEN COUNTY JAIL",
        f"{name} is no longer listed in the Allen County Jail roster.",
    ]
    if details:
        sections.append(" ".join(details))
    sections.extend([
        f"Roster checked: {local:%Y-%m-%d %I:%M %p %Z}",
        "Follow this Page for Allen County booking and roster-status updates.",
        "#FortWayne #AllenCounty",
        DISCLAIMER,
    ])
    return "\n\n".join(sections)


def _candidates(conn: sqlite3.Connection, destination: str,
                now: datetime, lookback_hours: float) -> list[dict]:
    """Confirmed departures inside the lookback window with no release post yet."""
    rows = conn.execute("""
        SELECT record_hash, name, image_url, charge, source_url, status_changed_at
        FROM custody_posts
        WHERE destination = ? AND desired_status = 'no_longer_listed'
          AND applied_status = 'no_longer_listed' AND status_changed_at IS NOT NULL
        ORDER BY status_changed_at, record_hash
    """, (destination,)).fetchall()
    cutoff = now - timedelta(hours=lookback_hours)
    result = []
    for record_hash, name, image_url, charge, source_url, changed in rows:
        try:
            changed_at = datetime.fromisoformat(changed)
        except (TypeError, ValueError):
            continue
        if changed_at.utcoffset() is None or not cutoff <= changed_at <= now:
            continue
        existing = conn.execute(
            "SELECT status FROM release_posts WHERE record_hash = ? AND destination = ?",
            (record_hash, destination),
        ).fetchone()
        if existing is not None:
            continue
        result.append({
            "record_hash": record_hash, "name": name, "image_url": image_url,
            "charge": charge, "source_url": source_url, "status_changed_at": changed,
        })
    return result


def _enrich(conn: sqlite3.Connection, candidate: dict, destination: str) -> dict:
    """Fill photo, charge, and booking date from the person's group post."""
    person = {
        "name": candidate["name"], "image_url": candidate["image_url"],
        "charge": candidate["charge"], "booking_date": "",
    }
    if person["image_url"] and person["charge"]:
        return person
    for group in grouped.groups_for_record(conn, candidate["record_hash"], destination):
        for member in group["members"]:
            if member["hash"] != candidate["record_hash"]:
                continue
            person["image_url"] = person["image_url"] or member.get("image_url", "")
            person["charge"] = person["charge"] or member.get("charge", "")
            person["booking_date"] = member.get("booking_date", "")
            person["name"] = person["name"] or member.get("name", "")
            return person
    return person


def _reserve(conn: sqlite3.Connection, record_hash: str, destination: str) -> bool:
    with conn:
        cursor = conn.execute(
            "INSERT INTO release_posts (record_hash, destination, status) VALUES (?, ?, 'pending') "
            "ON CONFLICT(record_hash, destination) DO NOTHING",
            (record_hash, destination),
        )
    return cursor.rowcount == 1


def _mark(conn: sqlite3.Connection, record_hash: str, destination: str, status: str,
          post_id: str | None = None, error: str | None = None) -> None:
    with conn:
        cursor = conn.execute(
            "UPDATE release_posts SET status = ?, post_id = ?, error = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE record_hash = ? AND destination = ? "
            "AND status = 'pending'",
            (status, post_id, error, record_hash, destination),
        )
        if cursor.rowcount != 1:
            raise ValueError("No pending release post remains to update")


def retry_failed(conn: sqlite3.Connection, record_hash: str, destination: str) -> None:
    """Explicitly retry a definite rejection; ambiguous outcomes stay blocked."""
    with conn:
        cursor = conn.execute(
            "UPDATE release_posts SET status = 'pending', error = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE record_hash = ? AND destination = ? "
            "AND status = 'failed'",
            (record_hash, destination),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only a failed release post can be explicitly retried")


def _publish_one(client, city: dict, candidate: dict, person: dict, caption: str) -> str:
    image_url = person.get("image_url") or ""
    if image_url:
        downloaded = download_image(image_url, referer=candidate["source_url"])
        if downloaded:
            try:
                out = os.path.join(tempfile.gettempdir(),
                                   f"release_{candidate['record_hash'][:12]}.jpg")
                stamped = watermark.stamp(downloaded, city["logo"], out)
                if stamped:
                    return client.publish_photo(stamped, caption)
            finally:
                try:
                    os.unlink(downloaded)
                except FileNotFoundError:
                    pass
    return client.publish_message(caption)


def publish_pending(conn: sqlite3.Connection, client, city: dict, destination: str, *,
                    now: datetime | None = None,
                    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
                    dry_run: bool = False) -> dict:
    """Announce each newly confirmed departure exactly once.

    Preview counts the announcements that would be made without reserving
    ledger rows or calling Facebook. A departure whose announcement outcome
    is uncertain is reported and never retried automatically.
    """
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("The release check time must be timezone-aware")
    initialize(conn)
    grouped.initialize(conn)
    report = {"published": 0, "planned": 0, "errors": []}
    for candidate in _candidates(conn, destination, current, lookback_hours):
        person = _enrich(conn, candidate, destination)
        checked = datetime.fromisoformat(candidate["status_changed_at"])
        caption = render_release_caption(person, checked)
        if dry_run:
            report["planned"] += 1
            continue
        if not _reserve(conn, candidate["record_hash"], destination):
            continue
        try:
            post_id = _publish_one(client, city, candidate, person, caption)
            if not isinstance(post_id, str) or not post_id:
                raise FacebookDeliveryUnknown(
                    "Facebook accepted the release post without a usable post ID; "
                    "check the Page before retrying."
                )
        except FacebookDeliveryUnknown as exc:
            _mark(conn, candidate["record_hash"], destination, "uncertain", error=str(exc))
            report["errors"].append(f"{person['name']}: {exc}")
            continue
        except FacebookError as exc:
            _mark(conn, candidate["record_hash"], destination, "failed", error=str(exc))
            report["errors"].append(f"{person['name']}: {exc}")
            continue
        except Exception as exc:
            _mark(conn, candidate["record_hash"], destination, "failed",
                  error=f"Unexpected outcome: {exc}")
            report["errors"].append(f"{person['name']}: release post outcome needs review ({type(exc).__name__})")
            continue
        _mark(conn, candidate["record_hash"], destination, "published", post_id=post_id)
        report["published"] += 1
        log.info("Published release post %s for %s", post_id, person["name"])
    return report
