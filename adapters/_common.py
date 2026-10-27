"""Shared helpers for Playwright-based adapters."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

log = logging.getLogger("adapters.common")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def load_page_html(url: str, wait_selector: str, dump_path: Optional[str] = None,
                   timeout_ms: int = 45000) -> str:
    """Load a page with Playwright, wait for wait_selector, return the HTML."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except Exception:
                log.warning("Selector %r not found on %s; parsing whatever loaded", wait_selector, url)
            html = page.content()
        finally:
            browser.close()
    if dump_path:
        with open(dump_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        log.info("Dumped raw HTML to %s", dump_path)
    return html


def download_image(image_url: str, referer: str = "") -> Optional[str]:
    """Download an image to a temp file; return the path or None."""
    if not image_url:
        return None
    try:
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        resp = requests.get(image_url, headers=headers, timeout=30)
        resp.raise_for_status()
        ext = os.path.splitext(urlparse(image_url).path)[1] or ".jpg"
        fd, path = tempfile.mkstemp(suffix=ext, prefix="autopost_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(resp.content)
        return path
    except Exception as exc:
        log.warning("Image download failed for %s: %s", image_url, exc)
        return None


def parse_age(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,3})\b", text or "")
    if m:
        age = int(m.group(1))
        if 10 <= age <= 110:
            return age
    return None


def absolute(base: str, maybe_relative: str) -> str:
    return urljoin(base, maybe_relative) if maybe_relative else ""
