"""Update retained Facebook captions from complete daily roster observations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
from zoneinfo import ZoneInfo

from adapters.base import get_adapter
from core import custody

log = logging.getLogger("core.roster_monitor")
LOCAL_TIME = ZoneInfo("America/Indiana/Indianapolis")
STATUS_NOTE = "UPDATE: (RELEASED)"
BOOKING_LOCATION = "Fort Wayne & Allen County, Indiana"
BOOKING_AREA = (
    "Allen County booking update for Fort Wayne, New Haven, Huntertown, "
    "Leo-Cedarville, Grabill, Woodburn, and nearby communities."
)
BOOKING_FOLLOW = (
    "Follow this Page for source-linked Allen County booking and roster-status updates."
)
BOOKING_TAGS = "#FortWayne #AllenCounty"


def localize_caption(current: str, source_url: str) -> str:
    additions = []
    if BOOKING_AREA not in current:
        additions.append(BOOKING_AREA)
    if source_url and source_url not in current:
        additions.append(f"Public record source: {source_url}")
    if BOOKING_FOLLOW not in current:
        additions.append(BOOKING_FOLLOW)
    if BOOKING_TAGS not in current:
        additions.append(BOOKING_TAGS)
    if not additions:
        return current
    return "\\n".join(additions) + "\\n\\n" + current

MANAGED_NOTE = re.compile(
    r"\n\n(?:\(UPDATE: NO LONGER LISTED IN ALLEN COUNTY JAIL\)|UPDATE: \(RELEASED\))"
    r"(?:\nFor custody information: Allen County Jail \(260\) 449-7376\.)?"
    r"\nRoster checked: [^\n]+\nSource: https?://[^\s]+"
)


def source_key(city: dict, source: dict) -> str:
    return json.dumps([city["workspace_key"], source["adapter"], source["url"]], separators=(",", ":"))


def status_caption(current: str, candidate: dict) -> str:
    """Replace only the app's status note, retaining the existing booking text."""
    base = MANAGED_NOTE.sub("", current)
    if candidate["target_status"] == "listed":
        if STATUS_NOTE in base:
            raise ValueError("The app's roster note was edited; review it before acknowledging the reappearance")
        return base
    if candidate["target_status"] != "no_longer_listed":
        raise ValueError("Unsupported roster status")
    checked = datetime.fromisoformat(candidate["status_changed_at"]).astimezone(LOCAL_TIME)
    return (f"{base}\n\n{STATUS_NOTE}\n"
            f"For custody information: Allen County Jail (260) 449-7376.\n"
            f"Roster checked: {checked:%Y-%m-%d %I:%M %p %Z}\n"
            f"Source: {candidate['source_url']}")


def reconcile_city(city: dict, conn: sqlite3.Connection, client, hash_fields,
                   destination: str, *, now: datetime | None = None,
                   dry_run: bool = False) -> dict:
    """Read current bookings, then edit confirmed Page posts without resharing.

    The caller supplies the same record hash function used for publishing.
    Preview uses a database copy so scans and imported metadata are not saved.
    """
    clock = now or datetime.now(timezone.utc)
    work = conn
    if dry_run:
        work = sqlite3.connect(":memory:")
        conn.backup(work)
    report = {"updated": 0, "planned": 0, "errors": []}
    supported = 0
    try:
        for source in city["sources"]:
            if source["adapter"] != "allen_sheriff":
                continue
            supported += 1
            key = source_key(city, source)
            try:
                adapter = get_adapter(source["adapter"])
                rows = adapter.snapshot_rows(source["url"])
                if not adapter.snapshot_complete:
                    raise RuntimeError("The adapter did not confirm a complete roster")
                members = {
                    hash_fields(row["name"], row["booking_date"], row["detail_url"]): row
                    for row in rows
                }
                # Older releases cannot be inferred from an empty or partial source.
                if not members:
                    raise RuntimeError("Refusing an empty roster for status updates")
                for identity, row in members.items():
                    tracked = work.execute(
                        "SELECT 1 FROM custody_posts WHERE record_hash=? AND destination=?",
                        (identity, destination),
                    ).fetchone()
                    if tracked:
                        continue
                    receipt = work.execute(
                        "SELECT remote_ref FROM deliveries WHERE record_hash=? AND destination=? "
                        "AND status='published'", (identity, destination),
                    ).fetchone()
                    if receipt and receipt[0]:
                        original = client.get_caption(receipt[0])
                        custody.track(work, identity, destination, key, receipt[0], original,
                                      row["detail_url"], row["name"])
                unmapped = work.execute(
                    "SELECT COUNT(*) FROM deliveries d LEFT JOIN custody_posts c "
                    "ON c.record_hash=d.record_hash AND c.destination=d.destination "
                    "WHERE d.destination=? AND d.status='published' AND c.record_hash IS NULL",
                    (destination,),
                ).fetchone()[0]
                if unmapped:
                    log.warning("%s earlier confirmed posts have no roster/source mapping; "
                                "they cannot be updated automatically", unmapped)
                candidates = custody.observe(work, key, set(members), clock)
                tracked = work.execute(
                    "SELECT remote_ref FROM custody_posts WHERE source_key=? AND destination=?",
                    (key, destination),
                ).fetchall()
                for (remote_ref,) in tracked:
                    try:
                        current = client.get_caption(remote_ref)
                        caption = localize_caption(current, source["url"])
                        if caption == current:
                            continue
                        report["planned"] += 1
                        if dry_run:
                            continue
                        client.update_caption(remote_ref, caption, expected_caption=current)
                        report["updated"] += 1
                    except Exception as exc:
                        report["errors"].append(f"Post {remote_ref}: {exc}")
                observation = work.execute("SELECT checked_at FROM custody_scans WHERE source_key=?", (key,)).fetchone()
                accepted = observation and datetime.fromisoformat(observation[0]) == clock.astimezone(timezone.utc)
            except Exception as exc:
                report["errors"].append(f"{source['adapter']}: {exc}")
                continue
            for candidate in candidates:
                if candidate["destination"] != destination:
                    continue
                try:
                    current = client.get_caption(candidate["remote_ref"])
                    candidate["source_url"] = source["url"]
                    caption = status_caption(current, candidate)
                    report["planned"] += 1
                    if dry_run:
                        log.info("PREVIEW: would update existing post %s to %s",
                                 candidate["remote_ref"], candidate["target_status"])
                        continue
                    if caption != current:
                        client.update_caption(candidate["remote_ref"], caption, expected_caption=current)
                    custody.mark_applied(work, candidate["record_hash"], destination,
                                         candidate["target_status"], clock)
                    report["updated"] += 1
                    log.info("Updated existing post %s to %s",
                             candidate["remote_ref"], candidate["target_status"])
                except Exception as exc:
                    report["errors"].append(f"Post {candidate['remote_ref']}: {exc}")
        if not supported:
            report["errors"].append("No Allen County roster source configured for daily checks")
    finally:
        if dry_run:
            work.close()
    return report
