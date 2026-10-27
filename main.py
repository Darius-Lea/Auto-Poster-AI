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

from adapters._common import download_image
from adapters.base import Record, get_adapter
from core import caption as caption_mod
from core import notify, publisher, watermark

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
    return cities


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id_hash TEXT PRIMARY KEY)")
    conn.commit()
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
    for source in city["sources"]:
        try:
            adapter = get_adapter(source["adapter"], dump_html=args.dump_html)
            adapter.seen_check = lambda n, d, u: is_seen(conn, _hash(n, d, u))
            records = adapter.fetch(source["url"])
        except Exception as exc:
            log.error("Adapter %s failed: %s", source.get("adapter"), exc)
            errors[name].append(f"{source.get('adapter')}: {exc}")
            continue
        log.info("Adapter %s returned %d record(s)", source["adapter"], len(records))
        for rec in records:
            h = record_hash(rec)
            if is_seen(conn, h):
                continue
            if not args.dry_run:
                mark_seen(conn, h)
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
                    ref = publisher.publish(rec, stamped, cap, city["workspace_key"])
                    if not ref:
                        raise RuntimeError("publish returned no result")
                results[name].append({"name": rec.name, "charge": rec.charge, "image": stamped, "caption": cap})
            except Exception as exc:
                log.error("Record %s failed: %s", rec.name, exc)
                errors[name].append(f"{rec.name}: {exc}")
                if not args.dry_run:
                    unmark(conn, h)  # retry on the next run


def main() -> int:
    load_dotenv()
    setup_logging()
    parser = argparse.ArgumentParser(description="Auto Post pipeline")
    parser.add_argument("--city", help="Only run this city (display name or workspace_key)")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and caption, but do not publish")
    parser.add_argument("--dump-html", action="store_true", help="Save raw page HTML for selector debugging")
    args = parser.parse_args()

    try:
        cities = load_config(CONFIG_PATH)
    except Exception as exc:
        logging.getLogger("main").error("Fatal config error: %s", exc)
        return 1

    if args.city:
        wanted = args.city.lower()
        cities = [c for c in cities if wanted in (c["city"].lower(), c["workspace_key"].lower())]
        if not cities:
            logging.getLogger("main").error("No city matches %r", args.city)
            return 1

    conn = init_db()
    results: dict = {}
    errors: dict = {}
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
