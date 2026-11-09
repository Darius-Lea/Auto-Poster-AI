"""Indiana most-wanted listing adapter (TheMostWanted.net).

The county page (e.g. /Indiana/Allen/Browse/New) is a plain list of names,
each linking to a person page (/Indiana/Allen/View/<id>) that carries the
photo, date of birth, and the "Wanted For" warrant lines. Like the Solve the
Case adapter, this adapter walks the detail pages so the published record
includes the actual outstanding warrant charges.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from adapters._common import UA, absolute, load_page_html
from adapters.base import Record, ScraperAdapter

log = logging.getLogger("adapters.indiana_most_wanted")

VIEW_LINK = re.compile(r"/View/(\d+)", re.IGNORECASE)
NEW_COUNT = re.compile(r"(\d+)\s+New Warrants Found", re.IGNORECASE)
WARRANT_LINE = re.compile(r"Warrant\s*#\s*([0-9A-Za-z-]+)", re.IGNORECASE)
DOB_LINE = re.compile(r"\bDOB:\s*(\d{1,2}/\d{1,2}/\d{4})")
# Footer/navigation lines that can follow the "Wanted For" section in page text.
STOP_LINES = (
    "the most wanted network", "help us grow", "encourage your sheriff",
    "website design", "advertise with us", "read our disclaimer",
    "our disclaimer", "about us", "contact us",
)


def _flip_name(text: str) -> str:
    """Turn a listing name like 'Arellano, Eric' into 'Eric Arellano'."""
    text = " ".join((text or "").split())
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        if first and last:
            return f"{first} {last}"
    return text


def parse_wanted_links(html: str, base_url: str) -> list[dict]:
    """Extract each unique person-page URL and ID from the county listing."""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    seen = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        match = VIEW_LINK.search(href)
        if not match:
            continue
        detail_url = urljoin(base_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)
        result.append({
            "url": detail_url,
            "view_id": match.group(1),
            "name_hint": _flip_name(anchor.get_text(" ", strip=True)),
        })
    return result


def _age_from_dob(text: str, today: date) -> int | None:
    match = DOB_LINE.search(text or "")
    if not match:
        return None
    try:
        born = datetime.strptime(match.group(1), "%m/%d/%Y").date()
    except ValueError:
        return None
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if 10 <= age <= 110 else None


def _wanted_for(text: str) -> tuple[str, str]:
    """Return (charge text, first warrant number) from a detail page's text.

    The section looks like:
        Wanted For:
        Warrant # 26-005343 Failure To Appear : 2 Counts
    Inline markup can split a warrant number and its description across
    text nodes, so the section is parsed from whitespace-normalized text.
    Charges are kept faithful to the source, with the warrant number moved
    into a trailing parenthetical.
    """
    flat = " ".join((text or "").split())
    marker = re.search(r"Wanted For:\s*", flat, re.IGNORECASE)
    if not marker:
        return "", ""
    segment = flat[marker.end():]
    lowered = segment.casefold()
    cut = len(segment)
    for stop in STOP_LINES:
        index = lowered.find(stop)
        if index != -1:
            cut = min(cut, index)
    segment = segment[:cut].strip()
    entries = list(WARRANT_LINE.finditer(segment))
    if not entries:
        return segment, ""
    charges = []
    first_warrant = ""
    for position, entry in enumerate(entries):
        number = entry.group(1)
        end = entries[position + 1].start() if position + 1 < len(entries) else len(segment)
        description = segment[entry.end():end].strip()
        description = re.sub(r"\s+:\s*", ": ", description).strip(" :")
        charges.append(f"{description} (Warrant #{number})" if description
                       else f"Warrant #{number}")
        first_warrant = first_warrant or number
    return "; ".join(charges), first_warrant


def parse_wanted_detail(html: str, detail_url: str, view_id: str,
                        today: date | None = None) -> dict | None:
    """Parse one person page into record fields, or None when unusable."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    name = heading.get_text(" ", strip=True) if heading else ""
    if not name and soup.title and soup.title.string:
        name = soup.title.string.split("|")[0].strip()
    text = soup.get_text("\n", strip=True)
    charge, _warrant = _wanted_for(text)
    image_url = ""
    photo = re.compile(rf"/photos/{re.escape(view_id)}-\d+\.", re.IGNORECASE)
    fallback_photo = None
    for image in soup.select("img[src]"):
        src = image.get("src", "")
        if photo.search(src):
            image_url = absolute(detail_url, src)
            break
        if fallback_photo is None and "/images/counties/" in src and "/photos/" in src:
            fallback_photo = absolute(detail_url, src)
    image_url = image_url or fallback_photo or ""
    if not (name and charge and image_url):
        return None
    return {
        "name": name,
        "age": _age_from_dob(text, today or date.today()),
        "charge": charge,
        "image_url": image_url,
    }


class IndianaMostWantedAdapter(ScraperAdapter):
    name = "indiana_most_wanted"

    def fetch(self, url: str) -> list[Record]:
        self.errors: list[str] = []
        dump = "dump_indiana_most_wanted.html" if self.dump_html else None
        try:
            html = load_page_html(url, "a[href*='/View/']", dump_path=dump)
        except Exception as exc:
            raise RuntimeError(f"Could not load Indiana most-wanted source at {url}: {exc}") from exc
        links = parse_wanted_links(html, url)
        if not links:
            count = NEW_COUNT.search(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
            if count and int(count.group(1)) == 0:
                log.info("The listing reports zero new warrants on %s", url)
                return []
            raise RuntimeError(
                "No wanted-listing links found; verify this site's actual layout before enabling publishing"
            )

        seen = getattr(self, "seen_check", None)
        records: list[Record] = []
        limit = getattr(self, "max_per_run", 1)
        if limit < 1:
            raise ValueError("The per-run record limit must be positive")

        for item in links:
            if seen and seen(item["name_hint"], item["view_id"], item["url"]):
                continue
            try:
                response = requests.get(item["url"], headers={"User-Agent": UA}, timeout=30)
                response.raise_for_status()
                detail = parse_wanted_detail(response.text, item["url"], item["view_id"])
            except Exception as exc:
                message = f"Could not verify wanted listing {item['view_id']}: {type(exc).__name__}"
                self.errors.append(message)
                log.warning(message)
                continue
            if detail is None:
                self.errors.append(
                    f"Skipped wanted listing {item['view_id']}: missing a clear name, photo, or warrant charge"
                )
                continue
            if seen and seen(detail["name"], item["view_id"], item["url"]):
                continue
            records.append(Record(
                name=detail["name"],
                age=detail["age"],
                charge=detail["charge"],
                booking_date=item["view_id"],
                source_url=item["url"],
                image_url=detail["image_url"],
                kind="warrant",
            ))
            if len(records) >= limit:
                break
        log.info("Parsed %d record(s) from %s", len(records), url)
        return records
