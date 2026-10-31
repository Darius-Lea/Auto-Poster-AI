"""Neutral factual caption generation with Google Gemini."""
from __future__ import annotations

import logging
import os

import google.generativeai as genai

from adapters.base import Record

log = logging.getLogger("core.caption")

DISCLAIMER = "All individuals are presumed innocent until proven guilty."
BOOKING_LOCATION = "Fort Wayne & Allen County, Indiana"
BOOKING_AREA = (
    "Allen County booking update for Fort Wayne, New Haven, Huntertown, "
    "Leo-Cedarville, Grabill, Woodburn, and nearby communities."
)
BOOKING_FOLLOW = (
    "Follow this Page for Allen County booking and roster-status updates."
)
BOOKING_TAGS = "#FortWayne #AllenCounty"


def _booking_local_context(text: str) -> str:
    return f"{BOOKING_AREA}\n\n{text}\n\n{BOOKING_FOLLOW}\n\n{BOOKING_TAGS}"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

SYSTEM_PROMPT = (
    "You are a neutral public-records reporter for {city_name}. "
    "Write a 2\u20133 sentence factual news brief. Include the person\u2019s name, "
    "age if available, the charge, and the booking date. Do not include jokes, "
    "opinion, speculation, commentary, source labels, citations, or URLs. "
    "Do not say where the information came from. End with: "
    "\u2018" + DISCLAIMER + "\u2019"
)


def _fallback(record: Record) -> str:
    age = f", {record.age}," if record.age else ""
    if record.kind == "warrant":
        return (
            f"MOST WANTED FUGITIVE OF THE DAY — ALLEN COUNTY, INDIANA\n\n"
            f"Allen County’s Sheriff-linked wanted roster lists {record.name}{age} as wanted in connection with {record.charge}. "
            f"The listing covers Allen County, including Fort Wayne, New Haven, Huntertown, Leo-Cedarville, Grabill, and Woodburn. "
            f"Please send tips to law enforcement; do not approach. Listing status can change. {DISCLAIMER}\n\n"
            "Follow this Page for Allen County wanted-list updates.\n\n"
            "#FortWayne #FortWayneIndiana #AllenCounty #Indiana #MostWanted"
        )
    text = (f"{record.name}{age} was listed in public records for {record.charge} "
            f"(date: {record.booking_date or 'not stated'}). {DISCLAIMER}")
    return _booking_local_context(text)


def _case_caption(record: Record) -> str:
    kind = record.charge.casefold()
    tags = ["#FortWayne", "#FortWayneIndiana", "#AllenCounty", "#Indiana"]
    if "missing" in kind:
        notice = "missing-person case"
        tags.append("#MissingPerson")
        action = "If you have information, use the case page's tip instructions to reach investigators."
    elif "fugitive" in kind:
        notice = "fugitive case"
        tags.append("#MostWanted")
        action = "Do not approach or contact the person. Send information only through the case page's tip instructions."
    else:
        notice = "unsolved case"
        tags.append("#UnsolvedCase")
        action = "If you have firsthand information, use the case page's tip instructions to reach investigators."
    caption = (
        f"COLD CASE OF THE DAY — FORT WAYNE, ALLEN COUNTY, INDIANA\n\n"
        f"Solve the Case lists {record.name} in an open {notice} (Case #{record.booking_date}) in Fort Wayne. "
        f"Listing status can change. {action}\n\n"
        f"Case details and tip instructions: {record.source_url}\n\n"
        + " ".join(tags)
    )
    if "fugitive" in kind:
        caption += f"\n\n{DISCLAIMER}"
    return caption


def make_caption(record: Record, city_name: str) -> str:
    # Case notices are attributed to the user-selected listing and stay neutral.
    if record.kind == "case":
        return _case_caption(record)
    # Keep warrant descriptions literal: a wanted listing does not establish
    # that the person has been arrested, booked, or convicted.
    if record.kind == "warrant":
        return _fallback(record)
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        log.warning("GEMINI_API_KEY not set; using template caption")
        return _fallback(record)
    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel(MODEL, system_instruction=SYSTEM_PROMPT.format(city_name=city_name))
        prompt = (
            f"Name: {record.name}\n"
            f"Age: {record.age if record.age else 'not available'}\n"
            f"Charge: {record.charge}\n"
            f"Booking date: {record.booking_date or 'not available'}\n"
        )
        text = (model.generate_content(prompt).text or "").strip()
        if not text:
            return _fallback(record)
        # The source URL is for internal record tracking only; keep it out of captions.
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().casefold().startswith(("source:", "source -", "source—"))
        ).strip()
        if DISCLAIMER not in text:
            text = f"{text} {DISCLAIMER}"
        return _booking_local_context(text)
    except Exception as exc:
        log.warning("Gemini caption failed (%s); using template", exc)
        return _fallback(record)
