#!/usr/bin/env python3
"""Read-only integration evidence. Never posts, generates text or changes tokens.

By default this inspects process configuration only. --probe opts into bounded
GET requests to fixed official APIs. --workspace is required to inspect saved
customer accounts. Output contains no credentials, account names or Page IDs.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote, urlparse

AI = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models", "data"),
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models", "data"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models", "models"),
}
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z")
VERSION = re.compile(r"v[0-9]{1,2}\.[0-9]{1,2}\Z")
DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-sonnet-4-20250514", "gemini": "gemini-2.5-flash"}


def _https(value, callback=None):
    try:
        url = urlparse(value)
        return bool(url.scheme == "https" and url.hostname and not url.username
                    and not url.password and not url.fragment and not url.query
                    and (callback is None or url.path == callback))
    except ValueError:
        return False


def _service(name, env, required, *, invalid=(), capability):
    missing = [key for key in required if not env.get(key, "").strip()]
    return {"service": name, "capability": capability,
            "configured": not missing and not invalid,
            "missing_variables": missing, "invalid_settings": list(invalid),
            "account_verification": "not_checked", "workflow_verification": "not_checked"}


def configuration(env):
    """Only names and booleans escape this function; no secret values."""
    services = []
    invalid = []
    redirect = env.get("META_OAUTH_REDIRECT_URI", "")
    if redirect and not _https(redirect, "/facebook/callback"):
        invalid.append("META_OAUTH_REDIRECT_URI must use the app's /facebook/callback HTTPS route")
    if env.get("META_PAGES_ENCRYPTION_SECRET") and len(env["META_PAGES_ENCRYPTION_SECRET"]) < 32:
        invalid.append("META_PAGES_ENCRYPTION_SECRET must have at least 32 characters")
    if not VERSION.fullmatch(env.get("META_GRAPH_VERSION", "v26.0")):
        invalid.append("META_GRAPH_VERSION must be a Graph version such as v26.0")
    services.append(_service("facebook", env, ("META_APP_ID", "META_APP_SECRET", "META_OAUTH_REDIRECT_URI", "META_PAGES_ENCRYPTION_SECRET"), invalid=invalid,
                             capability="Customer Page authorization, explicit Page choice, approved posting and read-only post metrics"))
    order = [part.strip().lower() for part in env.get("AUTOPOST_AI_PROVIDERS", "openai,anthropic,gemini").split(",")]
    for name, (key, _, _) in AI.items():
        invalid = []
        if name not in order:
            invalid.append("Provider is excluded by AUTOPOST_AI_PROVIDERS")
        if not MODEL.fullmatch(env.get(name.upper() + "_MODEL", DEFAULT_MODELS[name]).strip()):
            invalid.append(name.upper() + "_MODEL has an invalid format")
        services.append(_service(name, env, (key,), invalid=invalid,
                                 capability="Managed text drafting with availability fallback; consumer chat subscriptions are separate"))
    invalid = []
    if env.get("CANVA_REDIRECT_URI") and not _https(env["CANVA_REDIRECT_URI"], "/connections/canva/callback"):
        invalid.append("CANVA_REDIRECT_URI must use the app's /connections/canva/callback HTTPS route")
    key = env.get("AUTOPOST_CONNECTOR_SECRET") or env.get("META_PAGES_ENCRYPTION_SECRET", "")
    if len(key) < 32:
        invalid.append("A stable AUTOPOST_CONNECTOR_SECRET or META_PAGES_ENCRYPTION_SECRET of at least 32 characters is required")
    services.append(_service("canva", env, ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REDIRECT_URI"), invalid=invalid,
                             capability="Customer OAuth, create design, open Canva editor, export own supported design"))
    for name, capability in (("capcut", "Caption and brand-guidance download for manual editor handoff"),
                              ("muse.ai", "External website handoff; customer library, analytics and automatic editing are not implemented")):
        services.append({"service": name, "configured": False, "capability": capability,
                         "account_verification": "unsupported", "workflow_verification": "handoff_only",
                         "missing_variables": [], "invalid_settings": []})
    return services


def _read_connections(database, workspace):
    # mode=ro is essential: diagnostic runs must not create/migrate a database.
    path = Path(database).resolve()
    if not path.is_file():
        return {"state": "database_not_found", "facebook": [], "canva": []}
    try:
        with sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            facebook = list(conn.execute("SELECT page_id,encrypted_access_token FROM connected_facebook_pages WHERE workspace_id=? LIMIT 500", (workspace,))) if "connected_facebook_pages" in tables else []
            canva = list(conn.execute("SELECT credentials FROM editor_connections WHERE workspace_id=? AND provider='canva' LIMIT 1", (workspace,))) if "editor_connections" in tables else []
        return {"state": "read_only", "facebook": facebook, "canva": canva}
    except sqlite3.Error:
        return {"state": "database_unreadable", "facebook": [], "canva": []}


def _decrypt(sealed, secret):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())).decrypt(sealed.encode() if isinstance(sealed, str) else sealed).decode()


def _clip_runtime(env):
    dependencies = {}
    for name in ("faster_whisper", "yt_dlp", "cv2", "PIL", "numpy"):
        try:
            dependencies[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            dependencies[name] = False
    for binary in ("ffmpeg", "ffprobe"):
        dependencies[binary] = shutil.which(binary) is not None
    root = Path(__file__).resolve().parents[2]
    dependencies["bundled_engine"] = (root / "features/clip_studio/engine/clip_engine.py").is_file()
    enabled = env.get("AUTOPOST_CLIP_ENABLED", "false").strip().lower() == "true"
    return {"service": "clip_studio", "configured": enabled and all(dependencies.values()),
            "enabled_by_operator": enabled, "runtime_dependencies_present": all(dependencies.values()),
            "runtime_dependencies": dependencies, "account_verification": "local_render_only",
            "workflow_verification": "not_checked", "render_verification": "not_checked",
            "capability": "Local video clipping, transcription, framing, captions and QA; runtime presence does not verify a render or social video publication",
            "notice": "Transcription model download/cache, CPU capacity, tenant integration, source permissions and video-capable publishing require separate checks"}


def _get(url, headers):
    """Only this function performs I/O. No response body or exception is reported."""
    import requests
    try:
        with requests.get(url, headers={"Accept": "application/json", **headers},
                          timeout=(5, 15), allow_redirects=False, stream=True) as response:
            content = bytearray()
            for chunk in response.iter_content(16384):
                content.extend(chunk)
                if len(content) > 1024 * 1024:
                    return {"http_status": response.status_code, "error": "response_too_large"}
            try:
                payload = json.loads(content) if content else None
            except (ValueError, UnicodeError):
                payload = None
            return {"http_status": response.status_code, "payload": payload}
    except requests.RequestException:
        return {"error": "network_or_tls_unavailable"}


def _verification(result, shape):
    safe = {key: result[key] for key in ("http_status", "error") if key in result}
    if result.get("error"):
        safe["account_verification"] = "inconclusive"
    elif result.get("http_status") in (401, 403):
        safe["account_verification"] = "authorization_or_network_policy_denied"
    elif result.get("http_status") == 200 and shape(result.get("payload")):
        safe["account_verification"] = "read_only_api_verified"
    else:
        safe["account_verification"] = "inconclusive"
    return safe


def check(env=None, *, probe=False, workspace=None, getter=None):
    env = os.environ if env is None else env
    getter = _get if getter is None else getter
    services = configuration(env)
    services.append(_clip_runtime(env))
    by_name = {item["service"]: item for item in services}
    records = _read_connections(env.get("AUTOPOST_WEB_DB", "autopost_web.db"), workspace) if workspace else None
    if records:
        for name in ("facebook", "canva"):
            by_name[name]["saved_accounts"] = len(records[name])
            by_name[name]["storage_inspection"] = records["state"]
    if probe:
        for name, (key, url, collection) in AI.items():
            if not by_name[name]["configured"]:
                continue
            headers = {"Authorization": "Bearer " + env[key]} if name == "openai" else {"x-api-key": env[key], "anthropic-version": "2023-06-01"} if name == "anthropic" else {"x-goog-api-key": env[key]}
            by_name[name].update(_verification(getter(url, headers), lambda data: isinstance(data, dict) and isinstance(data.get(collection), list)))
            by_name[name]["probe_scope"] = "Model catalog read only; generation, quotas and selected-model access remain unverified"
        if records and by_name["facebook"]["configured"]:
            results = []
            for page, sealed in records["facebook"]:
                if not isinstance(page, str) or not re.fullmatch(r"[0-9]{1,32}", page):
                    results.append({"account_verification": "stored_account_invalid"})
                    continue
                try:
                    token = _decrypt(sealed, env["META_PAGES_ENCRYPTION_SECRET"])
                except Exception:
                    results.append({"account_verification": "stored_token_unreadable"})
                    continue
                url = "https://graph.facebook.com/" + env.get("META_GRAPH_VERSION", "v26.0") + "/" + page + "?fields=id"
                results.append(_verification(getter(url, {"Authorization": "Bearer " + token}), lambda data: isinstance(data, dict) and data.get("id") == page))
            by_name["facebook"]["accounts_checked"] = len(results)
            by_name["facebook"]["accounts_verified"] = sum(r["account_verification"] == "read_only_api_verified" for r in results)
            by_name["facebook"]["account_verification"] = "no_saved_account" if not results else "read_only_api_verified" if all(r["account_verification"] == "read_only_api_verified" for r in results) else "incomplete"
            by_name["facebook"]["probe_scope"] = "Selected Page identity only; publishing permission, delivery and analytics require live acceptance checks"
        if records and by_name["canva"]["configured"]:
            entry = by_name["canva"]
            entry["account_verification"] = "no_saved_account"
            if records["canva"]:
                try:
                    secret = env.get("AUTOPOST_CONNECTOR_SECRET") or env.get("META_PAGES_ENCRYPTION_SECRET", "")
                    token = json.loads(_decrypt(records["canva"][0][0], secret))
                    if token.get("expires_at", 0) <= datetime.now(timezone.utc).timestamp() + 90:
                        entry["account_verification"] = "token_expired_reconnect_in_app"
                    elif isinstance(token.get("access_token"), str) and token["access_token"]:
                        entry.update(_verification(getter("https://api.canva.com/rest/v1/designs", {"Authorization": "Bearer " + token["access_token"]}), lambda data: isinstance(data, dict) and isinstance(data.get("items"), list)))
                    else:
                        entry["account_verification"] = "stored_token_unreadable"
                except Exception:
                    entry["account_verification"] = "stored_token_unreadable"
            entry["probe_scope"] = "Design catalog read only; no refresh, create, export, revoke or token writes"
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "probe_requested": probe,
            "workspace_selected": workspace is not None, "services": services,
            "notice": "Configuration and read-only API access are separate from complete customer workflow verification. This check never publishes, generates AI text, charges, sends mail, refreshes tokens or edits external content."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Read-only official API GET checks; no paid generation or posting")
    parser.add_argument("--workspace", help="Inspect only this workspace's saved connections (database remains read-only)")
    args = parser.parse_args()
    print(json.dumps(check(probe=args.probe, workspace=args.workspace), indent=2))


if __name__ == "__main__":
    main()
