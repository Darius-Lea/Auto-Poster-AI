"""Telegram notifications. Never raises."""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger("core.notify")
TIMEOUT = 10
MAX_NAMES = 5


def _creds() -> Optional[tuple[str, str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping")
        return None
    return token, chat


def send_telegram(message: str, image_paths: Optional[list[str]] = None) -> None:
    creds = _creds()
    if not creds:
        return
    token, chat = creds
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": message[:4000], "parse_mode": "Markdown"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            # Retry as plain text if Markdown parsing failed
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": message[:4000]},
                timeout=TIMEOUT,
            )
    except Exception as exc:
        log.warning("Telegram sendMessage failed: %s", exc)
    for item in image_paths or []:
        path, photo_caption = (item if isinstance(item, (tuple, list)) else (item, ""))
        try:
            with open(path, "rb") as fh:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat, "caption": (photo_caption or "")[:1000]},
                    files={"photo": fh},
                    timeout=30,
                )
        except Exception as exc:
            log.warning("Telegram sendPhoto failed for %s: %s", path, exc)


def _esc(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def send_telegram_summary(city_results: dict, errors: dict, dry_run: bool = False,
                          photos: Optional[list] = None) -> None:
    """city_results: {city: [ {"name":..., "charge":...}, ... ]}; errors: {city: [str, ...]}."""
    try:
        lines = ["*Auto Post run summary*" if not dry_run else "*[DRY RUN] Auto Post run summary*", ""]
        for city, items in city_results.items():
            lines.append(f"*{_esc(city)}*: {len(items)} new post(s)")
            for item in items[:MAX_NAMES]:
                lines.append(f"  \u2022 {_esc(item['name'])} \u2014 {_esc(item['charge'])}")
            if len(items) > MAX_NAMES:
                lines.append(f"  \u2022 +{len(items) - MAX_NAMES} more")
        if not city_results:
            lines.append("No cities processed.")
        flat = [(c, e) for c, errs in errors.items() for e in errs]
        if flat:
            lines += ["", "*Errors*"]
            for city, err in flat[:10]:
                lines.append(f"  \u2022 {_esc(city)}: {_esc(str(err))[:200]}")
        message = "\n".join(lines)
        if dry_run and not message.startswith("[DRY RUN]"):
            message = "[DRY RUN] " + message
        send_telegram(message, image_paths=photos)
    except Exception as exc:
        log.warning("Telegram summary failed: %s", exc)
