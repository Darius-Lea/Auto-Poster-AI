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
    # Hook first: name, age, booking date, and charges. The follow line,
    # the city list, and the hashtags go last, never up top.
    return f"{text}\n\n{BOOKING_FOLLOW}\n\n{BOOKING_AREA}\n\n{BOOKING_TAGS}"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

SYSTEM_PROMPT = (
    "You are a neutral public-records reporter for {city_name}. "
    "Write a 2\u20133 sentence factual news brief. The FIRST line is the hook: "
    "the person's name, age if available, booking date, and charge, for "
    "example 'Allen Harvey, 20, booked Nov 11, 2026 - unlawful carrying of "
    "a handgun with a prior conviction within 15 years, theft of a firearm, "
    "parole revocation.' Keep it short and human. Do not lead with a city "
    "list or an Allen County booking update, and never put the cities up "
    "top. Do not include jokes, "
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
            "#FortWayne #AllenCounty"
        )
    # Hook first: name, age, booking date, then charges.
    # Example: "Allen Harvey, 20, booked Nov 11, 2026 - unlawful carrying
    # of a handgun with a prior conviction within 15 years, theft of a
    # firearm, parole revocation."
    hook = record.name
    if record.age:
        hook += f", {record.age}"
    if record.booking_date:
        hook += f", booked {record.booking_date}"
    text = f"{hook} - {record.charge}. {DISCLAIMER}"
    return _booking_local_context(text)


def _case_caption(record: Record) -> str:
    kind = record.charge.casefold()
    # Only the two Page hashtags, at the very end. No extra hashtags.
    tags = ["#FortWayne", "#AllenCounty"]
    if "missing" in kind:
        notice = "missing-person case"
        action = "If you have information, use the case page's tip instructions to reach investigators."
    elif "fugitive" in kind:
        notice = "fugitive case"
        action = "Do not approach or contact the person. Send information only through the case page's tip instructions."
    else:
        notice = "unsolved case"
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
        # Never let a city list lead the caption: drop a leading
        # "Allen County booking update" line if Gemini put one up top.
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().casefold().startswith("allen county booking update")
        ).strip()
        if DISCLAIMER not in text:
            text = f"{text} {DISCLAIMER}"
        return _booking_local_context(text)
    except Exception as exc:
        log.warning("Gemini caption failed (%s); using template", exc)
        return _fallback(record)
