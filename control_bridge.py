"""GitHub Actions gate for the mobile control app.

With AUTOPOST_CONTROL_URL set, every scheduled live run must obtain an
entitlement + settings claim from the hosted control server. Any timeout or
HTTP error fails closed. The original owner workflow stays intact until the
control URL is explicitly configured; commercial deployments must set
AUTOPOST_REMOTE_REQUIRED=true.
"""
import json
import os
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MODES = {"publish", "publish-and-check", "publish-wanted", "publish-case", "check-status"}


def write_line(path, key, value):
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main():
    url = os.getenv("AUTOPOST_CONTROL_URL", "").rstrip("/")
    token = os.getenv("AUTOPOST_CONTROL_TOKEN", "")
    required = os.getenv("AUTOPOST_REMOTE_REQUIRED", "false").lower() == "true"
    mode = os.getenv("AUTOPOST_REQUESTED_MODE", "publish")
    if mode not in MODES:
        raise SystemExit("Unsupported requested mode")
    if not url:
        if required:
            raise SystemExit("Commercial publishing requires AUTOPOST_CONTROL_URL")
        data = {"run": True, "mode": mode, "limit": 1,
                "batch_size": os.getenv("BOOKING_BATCH_SIZE", "5"),
                "release_enabled": os.getenv("RELEASE_POSTS_ENABLED", "true") == "true"}
    else:
        host = urlparse(url)
        if host.scheme != "https" or not host.netloc or not token or len(token) < 32:
            raise SystemExit("Remote control requires HTTPS and a long worker token")
        req = Request(url + "/internal/legacy/claim",
                      data=json.dumps({"mode": mode}).encode("utf-8"),
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"},
                      method="POST")
        try:
            with urlopen(req, timeout=15) as response:
                data = json.load(response)
        except Exception as exc:
            raise SystemExit("Remote control unavailable; live posting blocked: " + str(exc))
    if not isinstance(data.get("run"), bool):
        raise SystemExit("Invalid remote control response")
    output = os.getenv("GITHUB_OUTPUT")
    write_line(output, "run", "true" if data["run"] else "false")
    write_line(output, "mode", data.get("mode", mode) if data["run"] else mode)
    write_line(output, "reason", str(data.get("reason", ""))[:140].replace("\n", " "))
    if data["run"]:
        selected = data.get("mode", mode)
        limit = int(data.get("limit", 1))
        batch = int(data.get("batch_size", 5))
        if selected not in MODES or not 1 <= limit <= 10 or not 0 <= batch <= 10:
            raise SystemExit("Invalid remote limits or publishing mode")
        write_line(output, "limit", str(limit))
        write_line(os.getenv("GITHUB_ENV"), "BOOKING_BATCH_SIZE", str(batch))
        write_line(os.getenv("GITHUB_ENV"), "RELEASE_POSTS_ENABLED",
                   "true" if data.get("release_enabled") is True else "false")
    print(("Approved: " if data["run"] else "Skipped: ")
          + str(data.get("reason", "Unconfigured owner's original schedule")))


if __name__ == "__main__":
    main()
