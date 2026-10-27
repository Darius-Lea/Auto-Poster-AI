"""Playwright login helper with persisted storage state."""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from playwright.sync_api import sync_playwright

log = logging.getLogger("core.auth")

# Selectors are best-effort; platforms change their markup often. # TODO: verify selectors
PRESETS = {
    "x": {
        "login_url": "https://x.com/i/flow/login",
        "username_selector": 'input[autocomplete="username"]',
        "next_selector": 'button:has-text("Next")',
        "password_selector": 'input[name="password"]',
        "submit_selector": 'button[data-testid="LoginForm_Login_Button"]',
        "success_selector": '[data-testid="SideNav_NewTweet_Button"]',
        "env_user": "X_USERNAME",
        "env_pass": "X_PASSWORD",
        "two_step": True,
    },
    "facebook": {
        "login_url": "https://www.facebook.com/login",
        "username_selector": "#email",
        "password_selector": "#pass",
        "submit_selector": 'button[name="login"]',
        "success_selector": '[aria-label="Create a post"], [role="feed"], [aria-label="Your profile"]',
        "env_user": "FACEBOOK_EMAIL",
        "env_pass": "FACEBOOK_PASSWORD",
        "two_step": False,
    },
}


COMBINED_STATE = os.path.join("storage_state", "combined.json")


def state_path_for(platform: str) -> str:
    """Prefer the combined state (hydrated from STORAGE_STATE_B64); else a per-platform file."""
    if os.path.isfile(COMBINED_STATE):
        return COMBINED_STATE
    return os.path.join("storage_state", f"{platform}.json")


def credentials_from_env(platform: str) -> dict:
    preset = PRESETS[platform]
    return {"username": os.getenv(preset["env_user"], ""), "password": os.getenv(preset["env_pass"], "")}


def ensure_logged_in(platform: str, login_url: Optional[str] = None,
                     storage_state_path: Optional[str] = None,
                     credentials: Optional[dict] = None,
                     selectors: Optional[dict] = None) -> Optional[str]:
    """Return a storage-state path usable by Playwright contexts, or None on failure.

    If the state file exists it is reused as-is. Otherwise a headless Chromium logs in
    with credentials (from env vars) and the resulting state is saved.
    """
    preset = {**PRESETS.get(platform, {}), **(selectors or {})}
    storage_state_path = storage_state_path or state_path_for(platform)
    login_url = login_url or preset.get("login_url")
    if os.path.isfile(storage_state_path):
        log.info("Reusing storage state for %s: %s", platform, storage_state_path)
        return storage_state_path
    credentials = credentials or credentials_from_env(platform)
    if not (credentials.get("username") and credentials.get("password")):
        log.error("No storage state and no credentials in env for %s", platform)
        return None
    if not login_url:
        log.error("No login_url for %s", platform)
        return None
    try:
        os.makedirs(os.path.dirname(storage_state_path) or ".", exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                page.fill(preset["username_selector"], credentials["username"])
                if preset.get("two_step"):
                    page.click(preset["next_selector"])
                page.wait_for_selector(preset["password_selector"], timeout=20000)
                page.fill(preset["password_selector"], credentials["password"])
                page.click(preset["submit_selector"])
                page.wait_for_selector(preset["success_selector"], timeout=45000)
                ctx.storage_state(path=storage_state_path)
                log.info("Saved storage state for %s to %s", platform, storage_state_path)
            finally:
                browser.close()
        return storage_state_path
    except Exception as exc:
        log.error("Login failed for %s: %s (CAPTCHA/2FA may require a manual login)", platform, exc)
        return None


def save_state_b64(storage_state_path: str) -> str:
    """Return the state file as a base64 string, ready to paste into a GitHub secret."""
    with open(storage_state_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def load_state_b64(b64: str, storage_state_path: str) -> str:
    """Write a base64 secret back to a storage-state file."""
    os.makedirs(os.path.dirname(storage_state_path) or ".", exist_ok=True)
    with open(storage_state_path, "wb") as fh:
        fh.write(base64.b64decode(b64))
    return storage_state_path


if __name__ == "__main__":
    # Usage: python -m core.auth <platform>   -> logs in, prints base64 state for your secret
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    plat = sys.argv[1] if len(sys.argv) > 1 else "x"
    path = ensure_logged_in(plat)
    if path:
        print(save_state_b64(path))
