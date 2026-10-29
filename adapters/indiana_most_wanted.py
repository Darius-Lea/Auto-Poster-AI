"""Indiana most-wanted listing adapter."""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from adapters._common import absolute, load_page_html, parse_age
from adapters.base import Record, ScraperAdapter

log = logging.getLogger("adapters.indiana_most_wanted")


class IndianaMostWantedAdapter(ScraperAdapter):
    name = "indiana_most_wanted"

    # TODO: verify selectors against live HTML
    WAIT_SELECTOR = ".wanted-card, .card, article, li.person"
    CARD_SELECTOR = ".wanted-card, .card, article, li.person"
    NAME_SELECTOR = "h2, h3, .name"
    AGE_SELECTOR = ".age"
    CHARGE_SELECTOR = ".charge, .offense, p"
    DATE_SELECTOR = ".date, time"
    IMG_SELECTOR = "img"

    def fetch(self, url: str) -> list[Record]:
        dump = "dump_indiana_most_wanted.html" if self.dump_html else None
        try:
            html = load_page_html(url, self.WAIT_SELECTOR, dump_path=dump)
        except Exception as exc:
            raise RuntimeError(f"Could not load Indiana most-wanted source at {url}: {exc}") from exc
        soup = BeautifulSoup(html, "html.parser")
        if not soup.select(self.CARD_SELECTOR):
            raise RuntimeError("No supported wanted-listing cards found; verify this site's actual layout before enabling publishing")
        records: list[Record] = []
        for card in soup.select(self.CARD_SELECTOR):
            try:
                name_el = card.select_one(self.NAME_SELECTOR)
                charge_el = card.select_one(self.CHARGE_SELECTOR)
                img_el = card.select_one(self.IMG_SELECTOR)
                age_el = card.select_one(self.AGE_SELECTOR)
                date_el = card.select_one(self.DATE_SELECTOR)
                if not (name_el and charge_el and img_el and img_el.get("src")):
                    log.warning("Skipping malformed card: %s", card.get_text(" ", strip=True)[:80])
                    continue
                records.append(Record(
                    name=name_el.get_text(" ", strip=True),
                    age=parse_age(age_el.get_text(strip=True)) if age_el else None,
                    charge=charge_el.get_text(" ", strip=True),
                    booking_date=date_el.get_text(" ", strip=True) if date_el else "listed",
                    source_url=url,
                    image_url=absolute(url, img_el["src"]),
                    kind="warrant",
                ))
            except Exception as exc:
                log.warning("Skipping card due to error: %s", exc)
        log.info("Parsed %d records from %s", len(records), url)
        if not records:
            raise RuntimeError("No valid wanted records parsed; source verification is required")
        return records
