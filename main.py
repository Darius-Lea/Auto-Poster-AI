"""Auto Post entry point."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()  # Adapter limits and DB settings are read during module import.

from adapters._common import download_image
from adapters.base import Record, get_adapter
from core import caption as caption_mod
from core import catchup_control, custody, delivery, grouped, notify, publisher, roster_monitor, watermark
from core.facebook import FacebookClient, FacebookDeliveryUnknown, FacebookError

DB_PATH = os.getenv("AUTOPOST_DB", "autopost.db")
CONFIG_PATH = os.path.join("config", "cities.json")


class CityAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['city']}] {msg}", kwargs


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def load_config(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        cities = json.load(fh)
    if not isinstance(cities, list) or not cities:
        raise ValueError("cities.json must be a non-empty list")
    for c in cities:
        for key in ("city", "workspace_key", "logo", "sources"):
            if key not in c:
                raise ValueError(f"City entry missing '{key}': {c}")
        for s in c["sources"]:
            if "adapter" not in s or "url" not in s:
                raise ValueError(f"Source missing adapter/url in {c['city']}")
            if "kind" in s and s["kind"] not in ("booking", "warrant", "case"):
                raise ValueError(f"Source kind must be booking or warrant in {c['city']}")
    return cities


def source_kind(source: dict) -> str:
    """Classify legacy sources while allowing explicit routing for new adapters."""
    return source.get("kind", "warrant" if source["adapter"] == "indiana_most_wanted" else "booking")


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id_hash TEXT PRIMARY KEY)")
    conn.commit()
    delivery.initialize(conn)
    custody.initialize(conn)
    grouped.initialize(conn)
    catchup_control.initialize(conn)
    posting_cadence.initialize(conn)
    return conn


def _hash(name: str, booking_date: str, source_url: str) -> str:
    raw = f"{name}|{booking_date}|{source_url}".lower().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def record_hash(rec: Record) -> str:
    return _hash(rec.name, rec.booking_date, rec.source_url)


def is_seen(conn: sqlite3.Connection, h: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE id_hash = ?", (h,)).fetchone() is not None


def mark_seen(conn: sqlite3.Connection, h: str) -> None:
    conn.execute("INSERT OR IGNORE INTO seen (id_hash) VALUES (?)", (h,))
    conn.commit()


def unmark(conn: sqlite3.Connection, h: str) -> None:
    conn.execute("DELETE FROM seen WHERE id_hash = ?", (h,))
    conn.commit()


def process_city(city: dict, conn: sqlite3.Connection, args, results: dict, errors: dict) -> None:
    name = city["city"]
    log = CityAdapter(logging.getLogger("main"), {"city": name})
    results.setdefault(name, [])
    errors.setdefault(name, [])
    destination = publisher.destination(city["workspace_key"])
    limit = getattr(args, "limit", None)
    for source in city["sources"]:
        selected_kind = getattr(args, "source_type", "all")
        kind = source_kind(source)
        if selected_kind != "all" and kind != selected_kind:
            continue
        if limit is not None and args.attempted >= limit:
            break
        if kind == "case" and not posting_cadence.is_due(conn):
            log.info("Skipping Fort Wayne case notice; the 24-hour posting interval has not elapsed")
            continue
        if kind in ("warrant", "case") and not args.dry_run and source.get("verified") is not True:
            errors[name].append(f"{kind.title()} source is not verified; preview and validate it before publishing")
            continue
        try:
            adapter = get_adapter(source["adapter"], dump_html=args.dump_html)
            if limit is not None:
                adapter.max_per_run = limit - args.attempted
            adapter.seen_check = lambda n, d, u: delivery.blocked(conn, _hash(n, d, u), destination)
            records = adapter.fetch(source["url"])
            errors[name].extend(getattr(adapter, "errors", []))
        except Exception as exc:
            log.error("Adapter %s failed: %s", source.get("adapter"), exc)
            errors[name].append(f"{source.get('adapter')}: {exc}")
            continue
        log.info("Adapter %s returned %d record(s)", source["adapter"], len(records))
        for rec in records:
            h = record_hash(rec)
            if delivery.blocked(conn, h, destination):
                continue
            if limit is not None:
                if args.attempted >= limit:
                    break
                args.attempted += 1
            claimed = False
            uploading = False
            try:
                img = download_image(rec.image_url, referer=rec.source_url)
                if not img:
                    raise RuntimeError("image download failed")
                out = os.path.join(tempfile.gettempdir(), f"stamped_{h[:12]}.jpg")
                stamped = watermark.stamp(img, city["logo"], out)
                if not stamped:
                    raise RuntimeError("watermark failed (missing logo or bad image)")
                cap = caption_mod.make_caption(rec, name)
                if args.dry_run:
                    log.info("DRY RUN: would publish %s | %s", rec.name, cap)
                else:
                    claimed = delivery.claim(conn, h, destination)
                    if not claimed:
                        continue
                    if rec.kind == "case":
                        posting_cadence.reserve_attempt(conn)
                    uploading = True
                    ref = publisher.publish(rec, stamped, cap, city["workspace_key"])
                    if not ref:
                        raise RuntimeError("publish returned no result")
                    delivery.complete(conn, h, destination, ref)
                    uploading = False
                    if destination.startswith("meta:") and source_kind(source) == "booking":
                        custody.track(conn, h, destination, roster_monitor.source_key(city, source),
                                      ref, cap, rec.source_url, rec.name)
                    log.info("Delivery confirmed: %s -> %s", h, ref)
                results[name].append({"name": rec.name, "charge": rec.charge, "image": stamped, "caption": cap})
            except FacebookDeliveryUnknown as exc:
                if claimed:
                    delivery.uncertain(conn, h, destination)
                message = f"{rec.name}: {exc} [record={h}, destination={destination}]"
                log.error(message)
                errors[name].append(message)
            except Exception as exc:
                log.error("Record %s failed: %s", rec.name, exc)
                errors[name].append(f"{rec.name}: {exc}")
                if claimed:
                    # If persisting the successful upload failed, preserve the claim:
                    # another run must not repeat a possibly accepted remote post.
                    if uploading and not isinstance(exc, FacebookError) and destination.startswith("meta:"):
                        delivery.uncertain(conn, h, destination)
                    else:
                        delivery.release(conn, h, destination)
                if rec.kind == "case" and not (
                        uploading and not isinstance(exc, FacebookError) and destination.startswith("meta:")):
                    posting_cadence.clear_failed_attempt(conn)


def main() -> int:
    load_dotenv()
    global DB_PATH
    DB_PATH = os.getenv("AUTOPOST_DB", "autopost.db")
    setup_logging()
    parser = argparse.ArgumentParser(description="Auto Post pipeline")
    parser.add_argument("--city", help="Only run this city (display name or workspace_key)")
    parser.add_argument("--source-type", choices=("booking", "warrant", "case", "all"), default="all",
                        help="Only run booking or warrant sources; default runs all configured sources")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and caption, but do not publish")
    parser.add_argument("--dump-html", action="store_true", help="Save raw page HTML for selector debugging")
    parser.add_argument("--check-facebook", action="store_true", help="Read-only Page token and identity check; does not post")
    parser.add_argument("--check-status", action="store_true", help="Check the current roster and update existing Facebook booking captions; no new posts")
    parser.add_argument("--limit", type=int, default=int(os.getenv("MAX_PER_RUN", "1")), help="Maximum records attempted across the entire run (default: MAX_PER_RUN or 1)")
    parser.add_argument("--deliveries", action="store_true", help="List pending/uncertain deliveries for reconciliation")
    parser.add_argument("--resolve-record", help="Hash of a pending/uncertain delivery to reconcile")
    parser.add_argument("--destination", help="Exact delivery destination from --deliveries")
    resolution = parser.add_mutually_exclusive_group()
    resolution.add_argument("--remote-ref", help="Existing Facebook post/photo ID after checking the Page")
    resolution.add_argument("--retry-confirmed-absent", action="store_true", help="Allow retry only after checking the post is absent from the Page")
    args = parser.parse_args()
    args.attempted = 0
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.deliveries or args.resolve_record:
        conn = init_db()
        try:
            if args.resolve_record:
                if not args.destination or not (args.remote_ref or args.retry_confirmed_absent):
                    parser.error("resolution requires --destination and either --remote-ref or --retry-confirmed-absent")
                delivery.resolve(conn, args.resolve_record, args.destination,
                                 remote_ref=args.remote_ref, retry=args.retry_confirmed_absent)
            print(json.dumps(delivery.list_unresolved(conn), indent=2))
        finally:
            conn.close()
        return 0
    if args.check_status and os.getenv("PUBLISH_BACKEND", "queue").lower() != "meta":
        logging.getLogger("main").error("Daily status checks require PUBLISH_BACKEND=meta")
        return 1
    if args.check_facebook or args.check_status or (not args.dry_run and os.getenv("PUBLISH_BACKEND", "queue").lower() == "meta"):
        try:
            page = FacebookClient.from_env().check_page()
            logging.getLogger("main").info("Facebook Page connected: %s (ID %s). This read check does not test posting permissions.", page.get("name", ""), page["id"])
        except FacebookError as exc:
            logging.getLogger("main").error("%s", exc)
            return 1
        if args.check_facebook:
            return 0

    try:
        cities = load_config(CONFIG_PATH)
        for city in cities:
            publisher.destination(city["workspace_key"])
    except Exception as exc:
        logging.getLogger("main").error("Fatal config error: %s", exc)
        return 1

    if args.city:
        wanted = args.city.lower()
        cities = [c for c in cities if wanted in (c["city"].lower(), c["workspace_key"].lower())]
        if not cities:
            logging.getLogger("main").error("No city matches %r", args.city)
            return 1

    if args.source_type != "all" and not any(
        source_kind(source) == args.source_type
        for city in cities for source in city["sources"]
    ):
        logging.getLogger("main").error(
            "No %s source configured for the selected cities; add and verify that source before running it.",
            args.source_type,
        )
        return 1

    conn = init_db()
    if args.check_status:
        failed = False
        try:
            client = FacebookClient.from_env()
            for city in cities:
                report = roster_monitor.reconcile_city(
                    city, conn, client, _hash, publisher.destination(city["workspace_key"]),
                    dry_run=args.dry_run,
                )
                logging.getLogger("main").info("[%s] Existing-post status check: %s updated, %s planned",
                                               city["city"], report["updated"], report["planned"])
                for error in report["errors"]:
                    failed = True
                    logging.getLogger("main").error("[%s] %s", city["city"], error)
        finally:
            conn.close()
        return 1 if failed else 0
    if not args.dry_run and any(catchup_control.blocks_normal(
            conn, publisher.destination(city["workspace_key"])) for city in cities):
        logging.getLogger("main").error("Grouped catch-up is active or paused. Finish or reconcile Booking Catch-Up before normal individual posting resumes.")
        conn.close()
        return 1
    results: dict = {}
    errors: dict = {}
    unresolved = delivery.list_unresolved(conn)
    if unresolved:
        errors["Delivery history"] = [
            f"{row['record_hash']} -> {row['destination']}: {row['status']}; check the Page and reconcile with --deliveries"
            for row in unresolved
        ]
    for city in cities:
        try:
            process_city(city, conn, args, results, errors)
        except Exception as exc:  # last-resort guard so one city never kills the run
            logging.getLogger("main").error("[%s] Unhandled error: %s", city["city"], exc)
            errors.setdefault(city["city"], []).append(str(exc))
    conn.close()

    errors = {k: v for k, v in errors.items() if v}
    photos = [(i["image"], i["caption"]) for items in results.values() for i in items][:10]
    notify.send_telegram_summary(results, errors, dry_run=args.dry_run, photos=photos)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
