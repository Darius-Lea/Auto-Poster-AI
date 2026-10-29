"""Route seasonal UTC schedules to bookings, wanted posts, or custody checks.

Posting runs at 9:17 a.m., 1:17 p.m., and 7:17 p.m. Fort Wayne time; the
status-only check runs at 8:17 a.m., and the separately enabled wanted post runs
at 4:17 p.m. The trigger's cron identity, rather than the current hour, selects
the mode so a delayed job can still run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo


DAYLIGHT_CRON = "17 13,17,23 * * *"
STANDARD_CRON = "17 0,14,18 * * *"
STATUS_DAYLIGHT_CRON = "17 12 * * *"
STATUS_STANDARD_CRON = "17 13 * * *"
WANTED_DAYLIGHT_CRON = "17 20 * * *"
WANTED_STANDARD_CRON = "17 21 * * *"
FORT_WAYNE = ZoneInfo("America/Indiana/Indianapolis")


def scheduled_mode(event_name: str, event_schedule: str,
                   now: datetime | None = None) -> str:
    """Return the action for the active seasonal cron, or an empty mode.

    ``now`` must be timezone-aware when supplied. Normal GitHub delays are
    supported; an exceptionally delayed run crossing a DST transition may skip.
    Manual workflow inputs override the default publish mode in the workflow.
    """
    if event_name != "schedule":
        return "publish"
    current = now if now is not None else datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    offset = current.astimezone(FORT_WAYNE).utcoffset()
    schedules = {
        timedelta(hours=-4): {
            DAYLIGHT_CRON: "publish", STATUS_DAYLIGHT_CRON: "check-status",
            WANTED_DAYLIGHT_CRON: "publish-wanted",
        },
        timedelta(hours=-5): {
            STANDARD_CRON: "publish", STATUS_STANDARD_CRON: "check-status",
            WANTED_STANDARD_CRON: "publish-wanted",
        },
    }.get(offset, {})
    return schedules.get(event_schedule, "")


def allow_run(event_name: str, event_schedule: str,
              now: datetime | None = None) -> bool:
    """Keep the existing boolean guard while routing checks separately."""
    return bool(scheduled_mode(event_name, event_schedule, now))


if __name__ == "__main__":
    mode = scheduled_mode(os.getenv("EVENT_NAME", ""), os.getenv("EVENT_SCHEDULE", ""))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"run={str(bool(mode)).lower()}\nmode={mode}\n")
