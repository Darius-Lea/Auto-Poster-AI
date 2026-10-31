"""Fort Wayne Police Department cases published through Solve the Case."""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from adapters._common import UA, load_page_html
from adapters.base import Record, ScraperAdapter

log = logging.getLogger("adapters.solve_the_case")
CASE_LINK = re.compile(r"/case/[^/]+/[^/?#]+")
CASE_NUMBER = re.compile(r"\bCASE\s*#\s*:?\s*([A-Za-z0-9-]+)", re.IGNORECASE)
CASE_KIND = re.compile(r"\b(MISSING PERSON|FUGITIVE|MURDER|HOMICIDE|SEXUAL ASSAULT|UNSOLVED)\b", re.IGNORECASE)


def _field(lines: list[str], label: str) -> str:
    normalized = label.rstrip(":").casefold()
    for index, line in enumerate(lines):
        if line.rstrip(":").casefold() == normalized:
            for value in lines[index + 1:]:
                if value.strip():
                    return value.strip()
        if line.casefold().startswith(normalized + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def _card_text(anchor) -> str:
    node = anchor
    best = anchor.get_text(" ", strip=True)
    for _ in range(4):
        parent = node.parent
        if parent is None:
            break
        text = parent.get_text(" ", strip=True)
        if text:
            best = text
        if "CASE" in text.upper() and ("SOLVED" in text.upper() or "OPEN" in text.upper()):
            return text
        node = parent
    return best


def parse_case_links(html: str, base_url: str) -> list[dict]:
    """Extract stable case identifiers and detail links from the agency page."""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    seen = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        if not CASE_LINK.search(href):
            continue
        detail_url = urljoin(base_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)
        card = _card_text(anchor)
        if re.search(r"\bSOLVED\b", card, re.IGNORECASE):
            continue
        text = anchor.get_text(" ", strip=True) or card
        number = CASE_NUMBER.search(card) or CASE_NUMBER.search(text)
        if not number:
            continue
        result.append({
            "url": detail_url,
            "case_number": number.group(1),
            "card_text": card,
            "title": text,
        })
    return result


class SolveTheCaseAdapter(ScraperAdapter):
    name = "solve_the_case"

    def fetch(self, url: str) -> list[Record]:
        self.errors: list[str] = []
        try:
            html = load_page_html(url, "a[href*='/case/']")
        except Exception as exc:
            raise RuntimeError(f"Could not load Solve the Case listings for Fort Wayne PD: {exc}") from exc
        links = parse_case_links(html, url)
        if not links:
            raise RuntimeError("No case links were found; refusing to treat a changed page as an empty case list")

        seen = getattr(self, "seen_check", None)
        records: list[Record] = []
        limit = getattr(self, "max_per_run", 1)
        if limit < 1:
            raise ValueError("The per-run record limit must be positive")

        for item in links:
            # Case number and detail URL make a stable identity before any detail request.
            name_hint = re.split(r"\s+(?:MISSING|FUGITIVE|MURDER|HOMICIDE)\b", item["title"], maxsplit=1,
                                 flags=re.IGNORECASE)[0].strip()
            if seen and seen(name_hint, item["case_number"], item["url"]):
                continue
            try:
                response = requests.get(item["url"], headers={"User-Agent": UA}, timeout=30)
                response.raise_for_status()
                detail = BeautifulSoup(response.text, "html.parser")
                text = detail.get_text("\n", strip=True)
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                status = _field(lines, "Status")
                jurisdiction = _field(lines, "Jurisdiction")
                if status.casefold() != "open":
                    continue
                if "fort wayne police department" not in jurisdiction.casefold():
                    continue

                heading = detail.find("h1")
                name = heading.get_text(" ", strip=True) if heading else name_hint
                match = CASE_KIND.search(text[:2500]) or CASE_KIND.search(item["title"])
                if not name or not match:
                    self.errors.append(f"Skipped case {item['case_number']}: missing a clear name or case type")
                    continue

                image_url = ""
                image_meta = detail.select_one('meta[property="og:image"]')
                if image_meta and image_meta.get("content"):
                    image_url = urljoin(item["url"], image_meta["content"])
                if not image_url:
                    for image in detail.select("img[src]"):
                        alt = (image.get("alt") or "").casefold()
                        src = image.get("src", "")
                        if name.casefold() in alt and src:
                            image_url = urljoin(item["url"], src)
                            break
                if not image_url:
                    self.errors.append(f"Skipped case {item['case_number']}: no case image was available")
                    continue

                records.append(Record(
                    name=name,
                    age=None,
                    charge=match.group(1).upper(),
                    booking_date=item["case_number"],
                    source_url=item["url"],
                    image_url=image_url,
                    kind="case",
                ))
                if len(records) >= limit:
                    break
            except Exception as exc:
                message = f"Could not verify case {item['case_number']}: {type(exc).__name__}"
                self.errors.append(message)
                log.warning(message)
        return records
