"""Allen County (IN) Sheriff jail roster adapter.

The list page (inmate-search) fills a table with JavaScript:
  Image | First Name | Last Name | Booking Date | Inmate Details (link)
Each details page (inmate-view/?id=...) is plain HTML with age and charges.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from adapters._common import UA
from adapters.base import Record, ScraperAdapter

log = logging.getLogger("adapters.allen_sheriff")

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "3"))      # only people booked in the last N days
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "10"))       # cap per run so a first run cannot flood
SKIP_UNDER_18 = os.getenv("SKIP_UNDER_18", "false").lower() == "true"  # default: post everyone on the roster
NO_CHARGE_TEXT = "Charges not listed at the time"


def _parse_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except Exception:
        return None


def parse_rows(html: str, base_url: str) -> list[dict]:
    """Parse the list table into dicts. Skips malformed rows."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("table tbody tr"):
        try:
            tds = tr.find_all("td")
            img = tr.find("img")
            link = tr.find("a", href=True)
            if len(tds) < 5 or not img or not img.get("src") or not link:
                continue
            first = tds[1].get_text(" ", strip=True)
            last = tds[2].get_text(" ", strip=True)
            booked = tds[3].get_text(" ", strip=True)
            if not (first and last and _parse_date(booked)):
                continue
            rows.append({
                "name": f"{first} {last}".title(),
                "booking_date": booked,
                "image_url": urljoin(base_url, img["src"]),
                "detail_url": urljoin(base_url, link["href"]),
            })
        except Exception as exc:
            log.warning("Skipping malformed row: %s", exc)
    return rows


def parse_detail(html: str) -> dict:
    """Return {'age': int|None, 'charges': [str, ...]} from an inmate-view page."""
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    age = None
    m = re.search(r"Age:\s*(\d{1,3})", text)
    if m:
        age = int(m.group(1))
    charges = []
    for c in re.findall(r"Statute Description\s*:?\s*([^\n]+)", text):
        c = c.strip().title()
        if c and c not in charges:
            charges.append(c)
    return {"age": age, "charges": charges}


class AllenSheriffAdapter(ScraperAdapter):
    name = "allen_sheriff"

    def _load_list_html(self, url: str) -> str:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context(user_agent=UA).new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector("table tbody tr td img", timeout=20000)
                except Exception:
                    # Rows may only appear after pressing Search.
                    try:
                        page.get_by_role("button", name="Search").click()
                        page.wait_for_selector("table tbody tr td img", timeout=20000)
                    except Exception:
                        log.warning("No rows appeared on %s", url)
                # Show as many rows as possible per page (best effort).
                try:
                    sel = page.locator("select[name$='_length']").first
                    values = sel.locator("option").evaluate_all("els => els.map(e => e.value)")
                    numeric = [int(v) for v in values if v.lstrip("-").isdigit()]
                    best = "-1" if -1 in numeric else str(max(numeric))
                    sel.select_option(best)
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                # If the oldest bookings are shown first, flip the sort (best effort).
                try:
                    dates = [_parse_date(d) for d in page.locator("table tbody tr td:nth-child(4)")
                             .all_inner_texts()]
                    dates = [d for d in dates if d]
                    if len(dates) > 1 and dates[0] < dates[-1]:
                        page.locator("table thead th", has_text="Booking Date").first.click()
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
                html = page.content()
            finally:
                browser.close()
        if self.dump_html:
            with open("dump_allen_sheriff.html", "w", encoding="utf-8") as fh:
                fh.write(html)
            log.info("Dumped raw HTML to dump_allen_sheriff.html")
        return html

    def fetch(self, url: str) -> list[Record]:
        try:
            html = self._load_list_html(url)
        except Exception as exc:
            log.error("Failed to load %s: %s", url, exc)
            return []
        rows = parse_rows(html, url)
        cutoff = date.today() - timedelta(days=MAX_AGE_DAYS)
        rows = [r for r in rows if _parse_date(r["booking_date"]) >= cutoff]
        rows.sort(key=lambda r: _parse_date(r["booking_date"]), reverse=True)
        log.info("%d row(s) booked since %s", len(rows), cutoff)

        records: list[Record] = []
        for r in rows[:MAX_PER_RUN]:
            try:
                resp = requests.get(r["detail_url"], headers={"User-Agent": UA}, timeout=30)
                resp.raise_for_status()
                detail = parse_detail(resp.text)
                time.sleep(1)  # be polite to the county's server
                charge_text = "; ".join(detail["charges"]) or NO_CHARGE_TEXT
                if SKIP_UNDER_18 and detail["age"] is not None and detail["age"] < 18:
                    log.info("Skipping a record: person is under 18")
                    continue
                records.append(Record(
                    name=r["name"],
                    age=detail["age"],
                    charge=charge_text,
                    booking_date=r["booking_date"],
                    source_url=r["detail_url"],
                    image_url=r["image_url"],
                ))
            except Exception as exc:
                log.warning("Skipping %s: %s", r.get("name"), exc)
        log.info("Built %d record(s) from %s", len(records), url)
        return records
