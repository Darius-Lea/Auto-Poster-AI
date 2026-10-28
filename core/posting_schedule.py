"""Select the UTC cron matching Fort Wayne's daylight-saving offset.

GitHub runs both cron expressions year-round. This guard allows only the one
that maps to 9:17 a.m., 1:17 p.m., and 7:17 p.m. in Fort Wayne. It checks the
trigger's cron identity rather than the current hour, so a delayed job can run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo


DAYLIGHT_CRON = "17 13,17,23 * * *"
STANDARD_CRON = "17 0,14,18 * * *"
FORT_WAYNE = ZoneInfo("America/Indiana/Indianapolis")


def allow_run(event_name: str, event_schedule: str,
              now: datetime | None = None) -> bool:
    """Allow manual runs, and the seasonal cron for scheduled runs.

    ``now`` must be timezone-aware when supplied. Normal GitHub delays are
    supported; an exceptionally delayed run crossing a DST transition may skip.
    """
    if event_name != "schedule":
        return True
    current = now if now is not None else datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    offset = current.astimezone(FORT_WAYNE).utcoffset()
    expected = {
        timedelta(hours=-4): DAYLIGHT_CRON,
        timedelta(hours=-5): STANDARD_CRON,
    }.get(offset)
    return expected is not None and event_schedule == expected


if __name__ == "__main__":
    permitted = allow_run(os.getenv("EVENT_NAME", ""), os.getenv("EVENT_SCHEDULE", ""))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"run={str(permitted).lower()}\n")
