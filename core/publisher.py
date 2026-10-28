"""Pluggable publishing backends: queue (default), browser, meta, postiz."""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import time
from dataclasses import asdict
from typing import Optional

import requests
from playwright.sync_api import sync_playwright

from adapters.base import Record
from core import auth
from core.facebook import FacebookClient, FacebookError

log = logging.getLogger("core.publisher")

BROWSER_PRESETS = {  # TODO: verify selectors against the live UI
    "x": {
        "url": "https://x.com/compose/post",
        "file_input": 'input[type="file"]',
        "text_box": '[data-testid="tweetTextarea_0"]',
        "submit": '[data-testid="tweetButton"]',
        "success": '[data-testid="toast"], [data-testid="SideNav_NewTweet_Button"]',
    },
    "facebook": {
        "url": "https://www.facebook.com/",
        "open_composer": '[aria-label="Create a post"], div[role="button"]:has-text("What\'s on your mind")',
        "file_input": 'input[type="file"][accept*="image"]',
        "text_box": 'div[role="textbox"]',
        "submit": 'div[aria-label="Post"][role="button"]',
        "success": '[role="feed"]',
    },
}


def _pause(lo: float = 0.8, hi: float = 2.5) -> None:
    time.sleep(random.uniform(lo, hi))


def _queue(record: Record, image_path: str, caption: str, workspace_key: str) -> Optional[str]:
    folder = os.path.join("outbox", workspace_key)
    os.makedirs(folder, exist_ok=True)
    stem = f"{int(time.time())}_{abs(hash((record.name, record.booking_date))) % 10**8}"
    img_dest = os.path.join(folder, stem + ".jpg")
    shutil.copyfile(image_path, img_dest)
    json_dest = os.path.join(folder, stem + ".json")
    with open(json_dest, "w", encoding="utf-8") as fh:
        json.dump({"caption": caption, "record": asdict(record), "image": os.path.basename(img_dest)},
                  fh, indent=2, ensure_ascii=False)
    return json_dest


def _browser(record: Record, image_path: str, caption: str, workspace_key: str) -> Optional[str]:
    platform = os.getenv("BROWSER_PLATFORM", "x").lower()
    preset = BROWSER_PRESETS.get(platform)
    if not preset:
        log.error("Unknown BROWSER_PLATFORM %r", platform)
        return None
    state = auth.ensure_logged_in(platform)
    if not state:
        return None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(storage_state=state)
            page = ctx.new_page()
            page.goto(preset["url"], wait_until="domcontentloaded", timeout=45000)
            _pause(2, 4)
            if preset.get("open_composer"):
                page.click(preset["open_composer"])
                _pause()
            page.wait_for_selector(preset["text_box"], timeout=20000)
            page.click(preset["text_box"])
            page.keyboard.type(caption, delay=random.randint(20, 60))
            _pause()
            page.set_input_files(preset["file_input"], image_path)
            _pause(2, 4)
            page.click(preset["submit"])
            page.wait_for_selector(preset["success"], timeout=30000)
            _pause()
            return f"browser:{platform}"
        finally:
            browser.close()


def _meta(record: Record, image_path: str, caption: str, workspace_key: str) -> Optional[str]:
    return "meta:" + FacebookClient.from_env().publish_photo(image_path, caption)


def _postiz(record: Record, image_path: str, caption: str, workspace_key: str) -> Optional[str]:
    base = os.getenv("POSTIZ_BASE_URL")
    key = os.getenv("POSTIZ_API_KEY")
    if not (base and key):
        log.error("POSTIZ_BASE_URL / POSTIZ_API_KEY not set")
        return None
    headers = {"Authorization": key}
    with open(image_path, "rb") as fh:
        up = requests.post(f"{base.rstrip('/')}/public/v1/upload", headers=headers,
                           files={"file": fh}, timeout=30)
    up.raise_for_status()
    media = up.json()
    resp = requests.post(
        f"{base.rstrip('/')}/public/v1/posts",
        headers=headers,
        json={"type": "now", "content": caption, "media": [media], "workspace": workspace_key},
        timeout=30,
    )
    resp.raise_for_status()
    return "postiz:ok"


BACKENDS = {"queue": _queue, "browser": _browser, "meta": _meta, "postiz": _postiz}


def destination(workspace_key: str) -> str:
    """Delivery identity includes the actual target, never its credentials."""
    backend = os.getenv("PUBLISH_BACKEND", "queue").lower()
    if backend not in BACKENDS:
        raise ValueError(f"Unknown PUBLISH_BACKEND {backend!r}; choose queue, meta, browser, or postiz")
    if backend == "meta":
        return f"meta:{FacebookClient.from_env().page_id}:{workspace_key}"
    if backend == "browser":
        return f"browser:{os.getenv('BROWSER_PLATFORM', 'x')}:{workspace_key}"
    if backend == "postiz":
        return f"postiz:{os.getenv('POSTIZ_BASE_URL', '').rstrip('/')}:{workspace_key}"
    return f"queue:{workspace_key}"


def publish(record: Record, image_path: str, caption: str, workspace_key: str) -> Optional[str]:
    """Publish once. Facebook errors propagate with their delivery outcome."""
    destination(workspace_key)  # Refuse typos instead of silently claiming a queue post succeeded.
    backend = os.getenv("PUBLISH_BACKEND", "queue").lower()
    fn = BACKENDS.get(backend)
    log.info("Publishing %s via %s", record.name, backend)
    try:
        ref = fn(record, image_path, caption, workspace_key)
        if ref:
            log.info("Published %s -> %s", record.name, ref)
        else:
            log.error("Publish returned nothing for %s via %s", record.name, backend)
        return ref
    except FacebookError:
        raise
    except Exception as exc:
        log.error("Publish failed for %s via %s: %s", record.name, backend, exc)
        return None
