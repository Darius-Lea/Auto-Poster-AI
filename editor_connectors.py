"""Supported creative-editor connections; credentials belong to the service/backend.

Canva OAuth and design/export requests follow Canva's official Connect OpenAPI.
Muse.ai and CapCut are clearly labelled handoffs until a customer authorization
API is verified and configured; a website link is never a connected account.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

API = "https://api.canva.com/rest/v1"
AUTH = "https://www.canva.com/api/oauth/authorize"
SCOPES = ("design:content:read", "design:content:write", "design:meta:read")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_REFRESH_LOCK = threading.RLock()


class ConnectorError(RuntimeError):
    def __init__(self, message, *, status=400, outcome_unknown=False):
        super().__init__(message)
        self.status = status
        self.outcome_unknown = outcome_unknown


def _db():
    c = sqlite3.connect(os.getenv("AUTOPOST_WEB_DB", "autopost_web.db"), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _workspace(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConnectorError("Unknown workspace")
    return value


def _cipher():
    key = os.getenv("AUTOPOST_CONNECTOR_SECRET") or os.getenv("META_PAGES_ENCRYPTION_SECRET", "")
    if len(key) < 32:
        raise ConnectorError("Editor connections are not available on this service yet", status=503)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _config():
    app_id = os.getenv("CANVA_CLIENT_ID", "").strip()
    secret = os.getenv("CANVA_CLIENT_SECRET", "")
    redirect = os.getenv("CANVA_REDIRECT_URI", "")
    parsed = urlparse(redirect)
    if (not app_id or not secret or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment or len(redirect) > 2000):
        raise ConnectorError("Canva connection is not available on this service yet", status=503)
    _cipher()
    return {"id": app_id, "secret": secret, "redirect_uri": redirect}


def init():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS editor_connections(
          workspace_id TEXT NOT NULL,provider TEXT NOT NULL,credentials BLOB NOT NULL,
          connected_at INTEGER NOT NULL,PRIMARY KEY(workspace_id,provider));
        CREATE TABLE IF NOT EXISTS editor_oauth_pending(
          state_hash TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,verifier_hash TEXT NOT NULL,
          expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS editor_designs(
          workspace_id TEXT NOT NULL,design_id TEXT NOT NULL,title TEXT NOT NULL,
          edit_url TEXT NOT NULL,view_url TEXT NOT NULL,created INTEGER NOT NULL,
          PRIMARY KEY(workspace_id,design_id));
        CREATE TABLE IF NOT EXISTS editor_exports(
          workspace_id TEXT NOT NULL,job_id TEXT NOT NULL,design_id TEXT NOT NULL,
          created INTEGER NOT NULL,PRIMARY KEY(workspace_id,job_id));
        """)


def status(workspace_id="owner"):
    _workspace(workspace_id)
    init()
    try:
        _config()
        ready = True
    except ConnectorError:
        ready = False
    with _db() as c:
        connected = c.execute("SELECT 1 FROM editor_connections WHERE workspace_id=? AND provider='canva'", (workspace_id,)).fetchone() is not None
    return {
        "canva": {"configured": ready, "connected": connected, "capability": "create_edit_export",
                  "label": "Canva", "message": "Create a design, edit in Canva and export a supported file." if ready else "Canva login will appear when the service connection is ready."},
        "muse": {"configured": False, "connected": False, "capability": "external_handoff",
                 "label": "Muse.ai", "url": "https://muse.ai/", "message": "Open your Muse.ai library. Account synchronization and analytics are not connected."},
        "capcut": {"configured": False, "connected": False, "capability": "export_handoff",
                   "label": "CapCut", "url": "https://www.capcut.com/", "message": "Download your content/caption package to use in CapCut. Automatic timeline editing is not connected."},
    }


def canva_authorize(workspace_id="owner", *, now=None):
    _workspace(workspace_id)
    conf = _config()
    init()
    now = int(time.time() if now is None else now)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with _db() as c:
        c.execute("DELETE FROM editor_oauth_pending WHERE expires<=?", (now,))
        c.execute("INSERT INTO editor_oauth_pending VALUES(?,?,?,?)", (
            hashlib.sha256(state.encode()).hexdigest(), workspace_id,
            hashlib.sha256(verifier.encode()).hexdigest(), now + 600))
    url = AUTH + "?" + urlencode({"client_id": conf["id"], "response_type": "code",
        "redirect_uri": conf["redirect_uri"], "scope": " ".join(SCOPES), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return {"url": url, "state": state, "verifier": verifier, "expires_at": now + 600}


def _response(method, path, *, token=None, body=None, form=None, oauth=False):
    headers = {"Accept": "application/json"}
    if oauth:
        conf = _config()
        encoded = base64.b64encode((conf["id"] + ":" + conf["secret"]).encode()).decode()
        headers["Authorization"] = "Basic " + encoded
    elif token:
        headers["Authorization"] = "Bearer " + token
    try:
        r = requests.request(method, API + path, headers=headers, json=body, data=form,
                             timeout=(5, 30), allow_redirects=False)
    except requests.RequestException:
        raise ConnectorError("Canva did not confirm the request. Check Canva before trying again.",
                             status=502, outcome_unknown=method == "POST" and not oauth) from None
    if not 200 <= r.status_code < 300:
        if r.status_code == 401:
            raise ConnectorError("Canva access expired. Reconnect your account.", status=401)
        if r.status_code == 403:
            raise ConnectorError("Canva did not allow this action. Check account permissions and plan.", status=403)
        raise ConnectorError("Canva could not complete this request. Try again later.",
                             status=502, outcome_unknown=r.status_code >= 500 and method == "POST" and not oauth)
    if not r.content:
        return {}
    if len(r.content) > 2 * 1024 * 1024:
        raise ConnectorError("Canva returned an oversized response", status=502)
    try:
        result = r.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ConnectorError("Canva returned an unreadable response", status=502) from None


def _save_token(workspace_id, payload, *, previous=None, now=None):
    now = int(time.time() if now is None else now)
    access, refresh, seconds = payload.get("access_token"), payload.get("refresh_token"), payload.get("expires_in")
    if not isinstance(access, str) or not 1 <= len(access) <= 8192:
        raise ConnectorError("Canva did not return valid authorization", status=502)
    if not refresh and previous:
        refresh = previous.get("refresh_token")
    if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
        raise ConnectorError("Canva did not return renewable authorization", status=502)
    if not isinstance(seconds, (int, float)) or not 0 < seconds <= 7 * 86400:
        raise ConnectorError("Canva returned an invalid authorization lifetime", status=502)
    value = {"access_token": access, "refresh_token": refresh, "expires_at": now + int(seconds)}
    sealed = _cipher().encrypt(json.dumps(value).encode())
    with _db() as c:
        c.execute("INSERT INTO editor_connections VALUES(?,'canva',?,?) ON CONFLICT(workspace_id,provider) DO UPDATE SET credentials=excluded.credentials,connected_at=excluded.connected_at", (workspace_id, sealed, now))
    return value


def canva_complete(workspace_id, code, verifier, expected_state, received_state, expires_at=None, *, now=None):
    _workspace(workspace_id)
    conf = _config()
    init()
    now = int(time.time() if now is None else now)
    if (not isinstance(expected_state, str) or not expected_state or not isinstance(received_state, str)
            or not hmac.compare_digest(expected_state, received_state)
            or not isinstance(verifier, str) or not 43 <= len(verifier) <= 128
            or not isinstance(code, str) or not 1 <= len(code) <= 4096):
        raise ConnectorError("Canva authorization expired or did not match. Connect again.", status=403)
    state_hash = hashlib.sha256(expected_state.encode()).hexdigest()
    with _db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM editor_oauth_pending WHERE state_hash=? AND workspace_id=? AND expires>?", (state_hash, workspace_id, now)).fetchone()
        if not row or not hmac.compare_digest(row["verifier_hash"], hashlib.sha256(verifier.encode()).hexdigest()):
            raise ConnectorError("Canva authorization expired or was already used. Connect again.", status=403)
        c.execute("DELETE FROM editor_oauth_pending WHERE state_hash=?", (state_hash,))
    result = _response("POST", "/oauth/token", form={"grant_type": "authorization_code", "code": code,
        "code_verifier": verifier, "redirect_uri": conf["redirect_uri"]}, oauth=True)
    _save_token(workspace_id, result, now=now)
    return {"connected": True, "provider": "canva"}


def _token(workspace_id, *, now=None):
    now = int(time.time() if now is None else now)
    with _REFRESH_LOCK:
        with _db() as c:
            row = c.execute("SELECT credentials FROM editor_connections WHERE workspace_id=? AND provider='canva'", (_workspace(workspace_id),)).fetchone()
        if not row:
            raise ConnectorError("Connect your Canva account first", status=409)
        try:
            value = json.loads(_cipher().decrypt(row["credentials"]))
        except (InvalidToken, ValueError, TypeError):
            raise ConnectorError("Canva connection could not be read. Reconnect your account.", status=409) from None
        if value["expires_at"] <= now + 90:
            result = _response("POST", "/oauth/token", form={"grant_type": "refresh_token", "refresh_token": value["refresh_token"]}, oauth=True)
            value = _save_token(workspace_id, result, previous=value, now=now)
        return value["access_token"]


def canva_disconnect(workspace_id="owner"):
    _workspace(workspace_id)
    init()
    with _db() as c:
        row = c.execute("SELECT credentials FROM editor_connections WHERE workspace_id=? AND provider='canva'", (workspace_id,)).fetchone()
        c.execute("DELETE FROM editor_connections WHERE workspace_id=? AND provider='canva'", (workspace_id,))
        c.execute("DELETE FROM editor_oauth_pending WHERE workspace_id=?", (workspace_id,))
    revoked = False
    if row:
        try:
            token = json.loads(_cipher().decrypt(row["credentials"]))["refresh_token"]
            _response("POST", "/oauth/revoke", form={"token": token}, oauth=True)
            revoked = True
        except (ConnectorError, InvalidToken, ValueError, KeyError):
            pass
    return {"connected": False, "revoked": revoked,
            "message": "Disconnected locally. You can also remove access in Canva account settings."}


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ConnectorError("Choose a valid Canva design or export")
    return value


def _canva_url(value):
    try:
        p = urlparse(value)
        if p.scheme == "https" and p.hostname and (p.hostname == "canva.com" or p.hostname.endswith(".canva.com")) and not p.username and not p.password and p.port in (None, 443):
            return value
    except (ValueError, TypeError):
        return ""
    return ""


def create_canva_design(workspace_id, title, width=1080, height=1080):
    _workspace(workspace_id)
    init()
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ConnectorError("Give the Canva design a name (up to 100 characters)")
    if (type(width) is not int or type(height) is not int or not 40 <= width <= 8000
            or not 40 <= height <= 8000 or width * height > 25000000):
        raise ConnectorError("Choose a supported design size")
    result = _response("POST", "/designs", token=_token(workspace_id), body={"type": "type_and_asset",
        "title": title.strip(), "design_type": {"type": "custom", "width": width, "height": height}})
    design = result.get("design") or {}
    design_id = _id(design.get("id"))
    urls = design.get("urls") or {}
    edit, view = _canva_url(urls.get("edit_url")), _canva_url(urls.get("view_url"))
    if not edit:
        raise ConnectorError("Canva did not return an editor link. Check your Canva designs before retrying.", status=502, outcome_unknown=True)
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO editor_designs VALUES(?,?,?,?,?,?)", (workspace_id, design_id, title.strip(), edit, view, int(time.time())))
    return {"id": design_id, "title": title.strip(), "edit_url": edit, "view_url": view}


def list_canva_designs(workspace_id="owner"):
    init()
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT design_id AS id,title,edit_url,view_url,created FROM editor_designs WHERE workspace_id=? ORDER BY created DESC LIMIT 100", (_workspace(workspace_id),))]


def _owned_design(workspace_id, design_id):
    with _db() as c:
        row = c.execute("SELECT 1 FROM editor_designs WHERE workspace_id=? AND design_id=?", (_workspace(workspace_id), _id(design_id))).fetchone()
    if not row:
        raise ConnectorError("That Canva design is not in your workspace", status=404)


def create_canva_export(workspace_id, design_id, kind="png"):
    init()
    _owned_design(workspace_id, design_id)
    if kind not in {"png", "jpg", "pdf", "mp4"}:
        raise ConnectorError("Choose PNG, JPG, PDF or MP4")
    result = _response("POST", "/exports", token=_token(workspace_id), body={"design_id": design_id, "format": {"type": kind}})
    job = result.get("job") or {}
    job_id = _id(job.get("id"))
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO editor_exports VALUES(?,?,?,?)", (workspace_id, job_id, design_id, int(time.time())))
    return _export_result(job)


def _export_result(job):
    state = job.get("status")
    if state not in {"in_progress", "success", "failed"}:
        raise ConnectorError("Canva returned an unknown export status", status=502)
    urls = [_canva_url(v) for v in (job.get("urls") or []) if isinstance(v, str)]
    return {"id": _id(job.get("id")), "status": state, "urls": [v for v in urls if v],
            "message": "Export ready; download links expire." if state == "success" else "Canva could not export this design." if state == "failed" else "Canva is preparing your export."}


def get_canva_export(workspace_id, job_id):
    init()
    with _db() as c:
        row = c.execute("SELECT 1 FROM editor_exports WHERE workspace_id=? AND job_id=?", (_workspace(workspace_id), _id(job_id))).fetchone()
    if not row:
        raise ConnectorError("That Canva export is not in your workspace", status=404)
    result = _response("GET", "/exports/" + job_id, token=_token(workspace_id))
    job = result.get("job") or {}
    if job.get("id") != job_id:
        raise ConnectorError("Canva returned a different export", status=502)
    return _export_result(job)
