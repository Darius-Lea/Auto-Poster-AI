"""Run one checkpointed ten-person catch-up post per GitHub matrix job."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

from core import catchup_control


def should_run(path: str, destination: str, session: str) -> bool:
    if not Path(path).is_file():
        return True
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catchup_controls'").fetchone()
        state = catchup_control.get(conn, destination) if exists else None
    if not state or state["session_id"] != session:
        return True
    if state["status"] == "paused":
        raise RuntimeError("Catch-up paused after an error; inspect its previous batch before continuing")
    return state["status"] != "completed"


def _output(name: str, value: str) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as target:
            target.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--verify-complete", action="store_true")
    args = parser.parse_args()
    path = os.getenv("AUTOPOST_DB", "autopost.db")
    workspace = os.getenv("CATCHUP_WORKSPACE", "fort-wayne")
    page_id = os.getenv("META_PAGE_ID", "")
    if not page_id:
        raise RuntimeError("META_PAGE_ID is required")
    destination = f"meta:{page_id}:{workspace}"
    session = f"{os.getenv('GITHUB_RUN_ID', 'local')}:{os.getenv('GITHUB_RUN_ATTEMPT', '1')}"
    if args.inspect_only:
        run = should_run(path, destination, session)
        _output("run", str(run).lower())
        print("Next catch-up batch needed" if run else "Catch-up already completed; skipping this batch")
        return 0
    if args.verify_complete:
        if should_run(path, destination, session):
            raise RuntimeError("The current roster is not fully covered yet. Rerun Booking Catch-Up to continue from saved history")
        print("Catch-up completed. Normal individual posting can resume when AUTOPOST_ENABLED=true.")
        return 0
    if os.getenv("GITHUB_REF") and os.environ["GITHUB_REF"] != "refs/heads/main":
        raise RuntimeError("Run Booking Catch-Up on main so it uses the live Page's delivery history")
    # Heavy application imports are deliberately after the dependency-free skip check.
    import main as app
    from core import catchup, grouped
    from core.facebook import FacebookClient

    app.setup_logging()
    if os.getenv("PUBLISH_BACKEND", "queue").lower() != "meta":
        raise RuntimeError("Booking Catch-Up requires PUBLISH_BACKEND=meta")
    client = FacebookClient.from_env()
    cities = app.load_config(app.CONFIG_PATH)
    selected = [city for city in cities if city["workspace_key"] == workspace]
    if len(selected) != 1:
        raise RuntimeError("Exactly one matching catch-up workspace must be configured")
    app.DB_PATH = path
    conn = app.init_db()
    try:
        grouped.initialize(conn)
        catchup_control.initialize(conn)
        state = catchup_control.begin(conn, destination, session)
        if state["status"] == "completed":
            return 0
        if state["status"] == "paused":
            raise RuntimeError("Inspect the previous catch-up batch before resuming")
        try:
            client.check_page()
            report = catchup.run_batch(selected[0], conn, client, app._hash,
                                       max_groups=1, include_previous=True)
        except Exception as exc:
            report = {"groups_published": 0, "people_published": 0, "remaining": None,
                      "complete": False, "paused": True, "errors": [str(exc)]}
        state = catchup_control.finish_batch(conn, destination, session, report)
        print(json.dumps(report, indent=2))
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as target:
                target.write(f"Catch-up: **{state['status']}**. "
                             f"This batch published {report['groups_published']} grouped post(s), "
                             f"covering {report['people_published']} people. "
                             f"Remaining current bookings: {report['remaining']}.\n")
                if report.get("notes"):
                    target.write("\\n\\nNotes:\\n")
                    for note in report["notes"]:
                        target.write(f"- {note}\\n")
        return 1 if state["status"] == "paused" else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
