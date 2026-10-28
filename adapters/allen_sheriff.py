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

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "0"))      # 0 includes every current roster booking
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "10"))       # cap per run so a first run cannot flood
SKIP_UNDER_18 = os.getenv("SKIP_UNDER_18", "false").lower() == "true"  # default: post everyone on the roster
NO_CHARGE_TEXT = "Charges not listed at the time"
MAX_PAGES = int(os.getenv("MAX_PAGES", "150"))            # safety cap on pagination clicks


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

    def _load_list_html(self, url: str) -> list[str]:
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
                        # A valid roster can be empty. A login, challenge, or error
                        # page without the roster table must not look like a quiet run.
                        if not page.locator("table").count():
                            raise RuntimeError("Roster table did not appear after loading and searching")
                        log.info("Roster table present but no image rows appeared on %s", url)
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
                # Walk every page of the roster (the site is paginated), collecting rows as we go.
                # Never treat an incomplete or repeated page walk as the complete roster.
                next_selector = (
                    "a.paginate_button.next, .paginate_button.next, a.next, "
                    "button.next, button.dt-paging-button.next"
                )

                def next_button():
                    nxt = page.locator(next_selector).first
                    if not nxt.is_visible():
                        return None
                    if ("disabled" in (nxt.get_attribute("class") or "")
                            or nxt.get_attribute("aria-disabled") == "true"
                            or nxt.get_attribute("disabled") is not None):
                        return None
                    return nxt

                htmls = [page.content()]
                previous = page.locator("table tbody").first.inner_html()
                visited = {previous}
                for _ in range(MAX_PAGES):
                    nxt = next_button()
                    if nxt is None:
                        break
                    try:
                        nxt.click()
                        page.wait_for_function(
                            "previous => { const body = document.querySelector('table tbody'); "
                            "return body && body.querySelector('tr td img') && body.innerHTML !== previous; }",
                            arg=previous, timeout=20000,
                        )
                    except Exception as exc:
                        raise RuntimeError("Could not advance to the next roster page; roster is incomplete") from exc
                    previous = page.locator("table tbody").first.inner_html()
                    if previous in visited:
                        raise RuntimeError("Roster pagination repeated an earlier page; roster is incomplete")
                    visited.add(previous)
                    htmls.append(page.content())
                else:
                    if next_button() is not None:
                        raise RuntimeError(f"Roster exceeds MAX_PAGES={MAX_PAGES} pagination clicks; increase the limit")
                log.info("Read %d page(s) of the roster", len(htmls))
                html = htmls
            finally:
                browser.close()
        if self.dump_html:
            with open("dump_allen_sheriff.html", "w", encoding="utf-8") as fh:
                fh.write(html[0])
            log.info("Dumped raw HTML (first page) to dump_allen_sheriff.html")
        return html

    def fetch(self, url: str) -> list[Record]:
        self.errors: list[str] = []
        try:
            pages = self._load_list_html(url)
        except Exception as exc:
            raise RuntimeError(f"Could not load Allen Sheriff roster at {url}: {exc}") from exc
        by_url: dict[str, dict] = {}
        for html in pages:
            for r in parse_rows(html, url):
                by_url[r["detail_url"]] = r
        rows = list(by_url.values())
        log.info("%d unique roster row(s) across all pages", len(rows))
        if MAX_AGE_DAYS < 0:
            raise ValueError("MAX_AGE_DAYS must be 0 (all current bookings) or a positive number")
        if MAX_AGE_DAYS:
            cutoff = date.today() - timedelta(days=MAX_AGE_DAYS)
            rows = [r for r in rows if _parse_date(r["booking_date"]) >= cutoff]
            log.info("%d row(s) booked since %s", len(rows), cutoff)
        else:
            log.info("All %d current roster row(s) eligible regardless of booking date", len(rows))
        # Work through the oldest remaining current bookings before new arrivals.
        # Re-reading the live roster each run avoids queuing people who left it.
        rows.sort(key=lambda r: (_parse_date(r["booking_date"]), r["detail_url"]))
        seen = getattr(self, "seen_check", None)
        if seen:
            rows = [r for r in rows if not seen(r["name"], r["booking_date"], r["detail_url"])]
            log.info("%d row(s) not posted yet", len(rows))

        records: list[Record] = []
        record_limit = getattr(self, "max_per_run", MAX_PER_RUN)
        if record_limit < 1:
            raise ValueError("The per-run record limit must be positive")
        for r in rows:
            if len(records) >= record_limit:
                break
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
                message = f"Could not fetch details for {r.get('name')}: {exc}"
                self.errors.append(message)
                log.warning(message)
        log.info("Built %d record(s) from %s", len(records), url)
        return records
