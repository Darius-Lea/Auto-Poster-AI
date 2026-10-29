"""Neutral factual caption generation with Google Gemini."""
from __future__ import annotations

import logging
import os

import google.generativeai as genai

from adapters.base import Record

log = logging.getLogger("core.caption")

DISCLAIMER = "All individuals are presumed innocent until proven guilty."
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

SYSTEM_PROMPT = (
    "You are a neutral public-records reporter for {city_name}. "
    "Write a 2\u20133 sentence factual news brief. Include the person\u2019s name, "
    "age if available, the charge, and the booking date. Do not include jokes, "
    "opinion, speculation, or commentary. End with: "
    "\u2018" + DISCLAIMER + "\u2019"
)


def _fallback(record: Record) -> str:
    age = f", {record.age}," if record.age else ""
    if record.kind == "warrant":
        return (
            f"MOST WANTED\n\nThe source lists {record.name}{age} as wanted in connection with {record.charge}. "
            f"Source: {record.source_url}. Listing status can change. {DISCLAIMER}"
        )
    return (f"{record.name}{age} was listed in public records for {record.charge} "
            f"(date: {record.booking_date or 'not stated'}). {DISCLAIMER}")


def make_caption(record: Record, city_name: str) -> str:
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
            f"Source: {record.source_url}"
        )
        text = (model.generate_content(prompt).text or "").strip()
        if not text:
            return _fallback(record)
        if DISCLAIMER not in text:
            text = f"{text} {DISCLAIMER}"
        return text
    except Exception as exc:
        log.warning("Gemini caption failed (%s); using template", exc)
        return _fallback(record)
