"""Authorized Facebook Business Page connections, not customer developer keys.

Single-owner mobile starter integration with tenant-scoped storage primitives.
The starter still requires production account identity and workspace ownership
before public customers can be onboarded.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet

WORKSPACE = "owner"
OAUTH_SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")
PAGE_ID = re.compile(r"[0-9]{1,32}\Z")
MAX_PAGES = 500
PENDING_SECONDS = 600


def _db():
    c = sqlite3.connect(os.getenv("AUTOPOST_WEB_DB", "autopost_web.db"), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    return c


def _cipher():
    key = os.getenv("META_PAGES_ENCRYPTION_SECRET", "")
    if len(key) < 32:
        raise RuntimeError("Set META_PAGES_ENCRYPTION_SECRET to a stable 32+ character secret")
    # Derive a valid Fernet key without exposing it. Do not rotate the secret
    # without first re-encrypting stored Page tokens.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def oauth_config():
    app_id = os.getenv("META_APP_ID", "").strip()
    app_secret = os.getenv("META_APP_SECRET", "")
    redirect_uri = os.getenv("META_OAUTH_REDIRECT_URI", "")
    if not app_id or not app_secret or not redirect_uri.startswith("https://"):
        raise RuntimeError("Configure META_APP_ID, META_APP_SECRET and HTTPS META_OAUTH_REDIRECT_URI")
    _cipher()
    return {"id": app_id, "secret": app_secret, "redirect_uri": redirect_uri,
            "version": os.getenv("META_GRAPH_VERSION", "v26.0")}


def configured():
    try:
        oauth_config()
        return True
    except RuntimeError:
        return False


def init():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS connected_facebook_pages (
          workspace_id TEXT NOT NULL,
          page_id TEXT NOT NULL,
          name TEXT NOT NULL,
          tasks_json TEXT NOT NULL,
          encrypted_access_token TEXT NOT NULL,
          connected_at INTEGER NOT NULL,
          PRIMARY KEY(workspace_id, page_id)
        );
        CREATE TABLE IF NOT EXISTS pending_facebook_pages (
          workspace_id TEXT NOT NULL,
          page_id TEXT NOT NULL,
          name TEXT NOT NULL,
          tasks_json TEXT NOT NULL,
          encrypted_access_token TEXT NOT NULL,
          expires_at INTEGER NOT NULL,
          PRIMARY KEY(workspace_id, page_id)
        );
        """)


def login_url(state: str):
    cfg = oauth_config()
    if not isinstance(state, str) or len(state) < 32:
        raise ValueError("Invalid OAuth state")
    params = {
        "client_id": cfg["id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "state": state,
    }
    business_config = os.getenv("META_FACEBOOK_LOGIN_CONFIG_ID", "").strip()
    if business_config:
        params["config_id"] = business_config
    else:
        params["scope"] = ",".join(OAUTH_SCOPES)
        params["auth_type"] = "rerequest"
    return "https://www.facebook.com/" + cfg["version"] + "/dialog/oauth?" + urlencode(params)


def _get_json(url, *, params=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        response = requests.get(url, params=params, headers=headers,
                                timeout=(10, 30), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        # Do not expose Meta auth codes, access tokens or URLs in error messages.
        raise RuntimeError("Meta authorization failed; please reconnect and try again") from None
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected response from Meta")
    return payload


def discover_pages(auth_code: str):
    """Exchange one-time code, then list Page tokens and permissions server-side."""
    cfg = oauth_config()
    if not auth_code or len(auth_code) > 8192:
        raise ValueError("Invalid authorization code")
    base = "https://graph.facebook.com/" + cfg["version"]
    payload = _get_json(
        base + "/oauth/access_token",
        params={"client_id": cfg["id"], "client_secret": cfg["secret"],
                "redirect_uri": cfg["redirect_uri"], "code": auth_code},
    )
    user_token = payload.get("access_token")
    if not isinstance(user_token, str) or not user_token:
        raise RuntimeError("Meta did not return a user authorization token")
    long_lived = _get_json(
        base + "/oauth/access_token",
        params={"grant_type": "fb_exchange_token",
                "client_id": cfg["id"], "client_secret": cfg["secret"],
                "fb_exchange_token": user_token},
    )
    token = long_lived.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Meta did not return an extended authorization token")
    pages = []
    after = None
    seen_cursors = set()
    for _ in range(15):
        params = {"fields": "id,name,tasks,access_token", "limit": 100}
        if after:
            params["after"] = after
        data = _get_json(base + "/me/accounts", params=params, token=token)
        batch = data.get("data", [])
        if not isinstance(batch, list):
            raise RuntimeError("Meta returned an invalid Page list")
        for row in batch:
            if not isinstance(row, dict):
                continue
            pid, name, page_token = row.get("id"), row.get("name"), row.get("access_token")
            if not isinstance(pid, str) or not PAGE_ID.fullmatch(pid):
                continue
            if not isinstance(page_token, str) or not page_token:
                continue
            if not isinstance(name, str) or not name:
                continue
            tasks = row.get("tasks", [])
            if not isinstance(tasks, list):
                tasks = []
            tasks = [t for t in tasks if isinstance(t, str)][:30]
            pages.append({"page_id": pid, "name": name[:160], "token": page_token,
                          "tasks": tasks, "can_publish": "CREATE_CONTENT" in tasks})
            if len(pages) > MAX_PAGES:
                raise RuntimeError("Too many Pages to process; contact support")
        cursor = data.get("paging", {}).get("cursors", {}).get("after")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            break
        if not data.get("paging", {}).get("next"):
            break
        seen_cursors.add(cursor)
        after = cursor
    else:
        raise RuntimeError("Unable to complete Facebook Page discovery")
    return pages


def remember_pending(workspace_id: str, pages: list, now=None):
    """Store Page choices briefly, encrypted, never in cookies or localStorage."""
    if not workspace_id or len(workspace_id) > 128:
        raise ValueError("Missing workspace")
    now = int(time.time()) if now is None else int(now)
    cipher = _cipher()
    with _db() as conn:
        conn.execute("DELETE FROM pending_facebook_pages WHERE workspace_id=?",
                     (workspace_id,))
        for p in pages:
            if not PAGE_ID.fullmatch(p["page_id"]) or not p.get("token"):
                raise ValueError("Invalid Facebook Page")
            conn.execute("""
                INSERT INTO pending_facebook_pages
                (workspace_id,page_id,name,tasks_json,encrypted_access_token,expires_at)
                VALUES(?,?,?,?,?,?)
            """, (workspace_id, p["page_id"], p["name"],
                  json.dumps(p["tasks"]), cipher.encrypt(p["token"].encode()).decode(),
                  now + PENDING_SECONDS))


def pending_choices(workspace_id: str, now=None):
    now = int(time.time()) if now is None else int(now)
    with _db() as c:
        rows = c.execute("""
            SELECT page_id,name,tasks_json FROM pending_facebook_pages
            WHERE workspace_id=? AND expires_at>? ORDER BY name
        """, (workspace_id, now)).fetchall()
    return [{"page_id": r["page_id"], "name": r["name"],
             "can_publish": "CREATE_CONTENT" in json.loads(r["tasks_json"])}
            for r in rows]


def activate_pages(workspace_id: str, page_ids: list[str], now=None):
    now = int(time.time()) if now is None else int(now)
    unique = list(dict.fromkeys(page_ids))
    if not unique or len(unique) > MAX_PAGES or any(
        not isinstance(x, str) or not PAGE_ID.fullmatch(x) for x in unique
    ):
        raise ValueError("Choose one or more valid Pages")
    with _db() as c:
        rows = c.execute("""
            SELECT * FROM pending_facebook_pages
            WHERE workspace_id=? AND expires_at>?
        """, (workspace_id, now)).fetchall()
        candidates = {r["page_id"]: r for r in rows}
        if any(x not in candidates or
               "CREATE_CONTENT" not in json.loads(candidates[x]["tasks_json"])
               for x in unique):
            raise PermissionError("One or more selected Pages lack publishing permission")
        for page_id in unique:
            p = candidates[page_id]
            c.execute("""
                INSERT INTO connected_facebook_pages
                (workspace_id,page_id,name,tasks_json,encrypted_access_token,connected_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(workspace_id,page_id) DO UPDATE SET
                  name=excluded.name,tasks_json=excluded.tasks_json,
                  encrypted_access_token=excluded.encrypted_access_token,
                  connected_at=excluded.connected_at
            """, (workspace_id, p["page_id"], p["name"], p["tasks_json"],
                  p["encrypted_access_token"], now))
        c.execute("DELETE FROM pending_facebook_pages WHERE workspace_id=?", (workspace_id,))
    return len(unique)


def list_pages(workspace_id: str):
    with _db() as c:
        rows = c.execute("""
            SELECT page_id,name,tasks_json,connected_at
            FROM connected_facebook_pages WHERE workspace_id=?
            ORDER BY name
        """, (workspace_id,)).fetchall()
    return [{"page_id": r["page_id"], "name": r["name"],
             "can_publish": "CREATE_CONTENT" in json.loads(r["tasks_json"]),
             "connected_at": r["connected_at"]} for r in rows]


def get_page_token(workspace_id: str, page_id: str):
    if not isinstance(page_id, str) or not PAGE_ID.fullmatch(page_id):
        raise PermissionError("Invalid Page selection")
    with _db() as c:
        row = c.execute("""
            SELECT encrypted_access_token,tasks_json FROM connected_facebook_pages
            WHERE workspace_id=? AND page_id=?
        """, (workspace_id, page_id)).fetchone()
    if not row or "CREATE_CONTENT" not in json.loads(row["tasks_json"]):
        raise PermissionError("Selected Page is not connected or lacks posting permission")
    return _cipher().decrypt(row["encrypted_access_token"].encode()).decode()


def disconnect(workspace_id: str, page_id: str):
    if not isinstance(page_id, str) or not PAGE_ID.fullmatch(page_id):
        raise ValueError("Invalid Page")
    with _db() as c:
        result = c.execute(
            "DELETE FROM connected_facebook_pages WHERE workspace_id=? AND page_id=?",
            (workspace_id, page_id),
        )
    return result.rowcount == 1


def publish_message(workspace_id: str, page_id: str, message: str):
    import app_control
    from core.facebook import FacebookClient
    if not app_control.allowed():
        raise PermissionError("Subscription required before Facebook publishing")
    token = get_page_token(workspace_id, page_id)
    return "meta:" + FacebookClient(page_id, token,
                  os.getenv("META_GRAPH_VERSION", "v26.0")).publish_message(message)
