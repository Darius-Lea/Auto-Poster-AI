#!/usr/bin/env python3
"""Read-only public deployment evidence; never certifies private workflows.

Uses inherited proxy/TLS configuration and follows no redirects. No login,
provider mutation, transcription request, charge or secret inspection occurs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import re
from urllib.parse import urlsplit

MAX_BODY = 512 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
PATHS = ("/welcome", "/install", "/account?mode=register",
         "/manifest.webmanifest", "/version", "/clips/jobs")


def validate_origin(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Supply the actual public HTTPS origin without whitespace.")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        if (url.scheme != "https" or not host or url.username or url.password
                or url.port not in (None, 443) or url.path not in ("", "/")
                or url.query or url.fragment or "\\" in value):
            raise ValueError
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid", ".example")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or "." not in host:
                raise ValueError
            if any(not label or label.startswith("-") or label.endswith("-") for label in host.split(".")):
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
        if host in ("example.com", "example.net", "example.org"):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Use the actual public HTTPS origin, without credentials, path, query or local address.") from None
    return "https://" + ("[" + host + "]" if ":" in host else host)


def _get(url):
    import requests
    try:
        with requests.get(url, timeout=(5, 15), allow_redirects=False, stream=True,
                          headers={"Accept": "application/json"}) as response:
            size = 0
            blocks = []
            for block in response.iter_content(chunk_size=16384):
                size += len(block)
                if size > MAX_BODY:
                    return {"http_status": response.status_code, "error": "response_too_large"}
                blocks.append(block)
            return {"http_status": response.status_code,
                    "content_type": response.headers.get("Content-Type", "").split(";", 1)[0].lower(),
                    "body": b"".join(blocks).decode("utf-8", errors="replace")}
    except requests.exceptions.ProxyError:
        return {"error": "proxy_route_unavailable"}
    except requests.RequestException:
        return {"error": "network_or_tls_unavailable"}


def check(origin, expected_commit=None, getter=None):
    origin = validate_origin(origin)
    if expected_commit is not None and not COMMIT.fullmatch(expected_commit):
        raise ValueError("Expected commit must be the full lowercase 40-character Git commit.")
    getter = getter or _get
    results = []
    deployed = None
    for path in PATHS:
        raw = getter(origin + path)
        status = raw.get("http_status")
        item = {"path": path, "http_status": status, "passed": False}
        if raw.get("error"):
            item["error"] = raw["error"]
        elif path == "/clips/jobs":
            item["passed"] = status == 401
            if status == 200:
                item["error"] = "private_job_endpoint_accessible_without_sign_in"
        elif path in ("/manifest.webmanifest", "/version"):
            try:
                payload = json.loads(raw.get("body", ""))
            except (ValueError, TypeError):
                payload = None
            if path == "/manifest.webmanifest":
                item["passed"] = bool(status == 200 and isinstance(payload, dict)
                    and payload.get("name") == "Auto Poster AI" and payload.get("icons")
                    and payload.get("scope") == "/")
            elif status == 200 and isinstance(payload, dict):
                value = payload.get("commit")
                deployed = value if isinstance(value, str) and COMMIT.fullmatch(value) else None
                item["passed"] = deployed is not None and (expected_commit is None or deployed == expected_commit)
                if deployed is None:
                    item["error"] = "deployment_revision_unknown"
                elif expected_commit and deployed != expected_commit:
                    item["error"] = "deployment_revision_mismatch"
        else:
            body = raw.get("body", "")
            item["passed"] = status == 200 and "Auto Poster AI" in body and "<html" in body.lower()
        results.append(item)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "origin": origin,
            "mode": "read_only_public_gets", "checks": results,
            "service_reachable": any(isinstance(item["http_status"], int) for item in results),
            "public_checks_passed": all(item["passed"] for item in results),
            "deployed_commit": deployed, "expected_commit": expected_commit,
            "deployed_revision_verified": bool(expected_commit and deployed == expected_commit),
            "private_workflow_verification": {name: "not_checked" for name in
                ("account_email", "logo_upload", "video_import", "fresh_transcription", "video_publication", "billing")},
            "ready_for_public_launch": False,
            "notice": "Public GET checks do not verify authenticated workflows, hosting capacity, provider approvals or a paid launch."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Actual public HTTPS origin; no path or credentials")
    parser.add_argument("--expected-commit", help="Full 40-character release commit")
    args = parser.parse_args()
    try:
        result = check(args.origin, args.expected_commit)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["public_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
