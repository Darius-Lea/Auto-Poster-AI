"""Allen County (IN) Sheriff booking/mugshot adapter."""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from adapters._common import absolute, load_page_html, parse_age
from adapters.base import Record, ScraperAdapter

log = logging.getLogger("adapters.allen_sheriff")


class AllenSheriffAdapter(ScraperAdapter):
    name = "allen_sheriff"

    # TODO: verify selectors against live HTML
    WAIT_SELECTOR = "table tbody tr"
    ROW_SELECTOR = "table tbody tr"
    NAME_SELECTOR = "td.name, td:nth-of-type(2)"
    AGE_SELECTOR = "td.age, td:nth-of-type(3)"
    CHARGE_SELECTOR = "td.charge, td:nth-of-type(4)"
    DATE_SELECTOR = "td.booking-date, td:nth-of-type(5)"
    IMG_SELECTOR = "img"

    def fetch(self, url: str) -> list[Record]:
        dump = "dump_allen_sheriff.html" if self.dump_html else None
        try:
            html = load_page_html(url, self.WAIT_SELECTOR, dump_path=dump)
        except Exception as exc:
            log.error("Failed to load %s: %s", url, exc)
            return []
        soup = BeautifulSoup(html, "html.parser")
        records: list[Record] = []
        for row in soup.select(self.ROW_SELECTOR):
            try:
                name_el = row.select_one(self.NAME_SELECTOR)
                charge_el = row.select_one(self.CHARGE_SELECTOR)
                date_el = row.select_one(self.DATE_SELECTOR)
                img_el = row.select_one(self.IMG_SELECTOR)
                age_el = row.select_one(self.AGE_SELECTOR)
                if not (name_el and charge_el and img_el and img_el.get("src")):
                    log.warning("Skipping malformed row: %s", row.get_text(" ", strip=True)[:80])
                    continue
                name = name_el.get_text(" ", strip=True)
                if not name:
                    continue
                records.append(Record(
                    name=name,
                    age=parse_age(age_el.get_text(strip=True)) if age_el else None,
                    charge=charge_el.get_text(" ", strip=True),
                    booking_date=date_el.get_text(" ", strip=True) if date_el else "",
                    source_url=url,
                    image_url=absolute(url, img_el["src"]),
                ))
            except Exception as exc:  # never crash the run
                log.warning("Skipping row due to error: %s", exc)
        log.info("Parsed %d records from %s", len(records), url)
        return records
