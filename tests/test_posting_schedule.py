from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from core.posting_schedule import (
    DAYLIGHT_CRON, STANDARD_CRON, STATUS_DAYLIGHT_CRON, STATUS_STANDARD_CRON,
    WANTED_DAYLIGHT_CRON, WANTED_STANDARD_CRON, FREQUENT_BOOKING_CRON,
    FORT_WAYNE, allow_run, scheduled_mode,
)


class PostingScheduleTests(unittest.TestCase):
    def test_manual_runs_are_allowed_without_a_cron(self):
        self.assertTrue(allow_run("workflow_dispatch", ""))
        self.assertEqual(scheduled_mode("workflow_dispatch", ""), "publish")

    def test_daily_status_crons_map_to_817_local_time(self):
        for month, expression in [(7, STATUS_DAYLIGHT_CRON), (1, STATUS_STANDARD_CRON)]:
            with self.subTest(month=month):
                fields = expression.split()
                local = datetime(2026, month, 15, int(fields[1]), int(fields[0]),
                                 tzinfo=timezone.utc).astimezone(FORT_WAYNE)
                self.assertEqual((local.hour, local.minute), (8, 17))

    def test_daily_wanted_crons_map_to_417_pm_local_time(self):
        for month, expression in [(7, WANTED_DAYLIGHT_CRON), (1, WANTED_STANDARD_CRON)]:
            with self.subTest(month=month):
                fields = expression.split()
                local = datetime(2026, month, 15, int(fields[1]), int(fields[0]),
                                 tzinfo=timezone.utc).astimezone(FORT_WAYNE)
                self.assertEqual((local.hour, local.minute), (16, 17))

    def test_wanted_cron_selects_only_wanted_and_correct_season(self):
        for month, wanted, wrong_wanted in [
            (7, WANTED_DAYLIGHT_CRON, WANTED_STANDARD_CRON),
            (1, WANTED_STANDARD_CRON, WANTED_DAYLIGHT_CRON),
        ]:
            with self.subTest(month=month):
                now = datetime(2026, month, 15, 22, tzinfo=timezone.utc)
                self.assertEqual(scheduled_mode("schedule", wanted, now), "publish-wanted")
                self.assertNotEqual(scheduled_mode("schedule", wanted, now), "publish")
                self.assertNotEqual(scheduled_mode("schedule", wanted, now), "check-status")
                self.assertTrue(allow_run("schedule", wanted, now))
                self.assertEqual(scheduled_mode("schedule", wrong_wanted, now), "")

    def test_status_cron_selects_only_status_and_correct_season(self):
        for month, status, posting, wrong_status in [
            (7, STATUS_DAYLIGHT_CRON, DAYLIGHT_CRON, STATUS_STANDARD_CRON),
            (1, STATUS_STANDARD_CRON, STANDARD_CRON, STATUS_DAYLIGHT_CRON),
        ]:
            with self.subTest(month=month):
                now = datetime(2026, month, 15, 20, tzinfo=timezone.utc)
                self.assertEqual(scheduled_mode("schedule", status, now), "check-status")
                self.assertTrue(allow_run("schedule", status, now))
                self.assertEqual(scheduled_mode("schedule", posting, now), "publish")
                self.assertEqual(scheduled_mode("schedule", wrong_status, now), "")
                self.assertEqual(scheduled_mode("schedule", "unexpected", now), "")

    def test_frequent_cron_publishes_and_checks_in_both_seasons(self):
        for month in (1, 7):
            with self.subTest(month=month):
                now = datetime(2026, month, 15, 12, tzinfo=timezone.utc)
                self.assertEqual(scheduled_mode("schedule", FREQUENT_BOOKING_CRON, now),
                                 "publish-and-check")
                self.assertTrue(allow_run("schedule", FREQUENT_BOOKING_CRON, now))

    def test_cli_writes_both_guard_and_mode_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "output"
            environment = dict(os.environ, EVENT_NAME="workflow_dispatch", EVENT_SCHEDULE="",
                               GITHUB_OUTPUT=str(output))
            subprocess.run([sys.executable, "-m", "core.posting_schedule"],
                           env=environment, check=True, cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(output.read_text(), "run=true\nmode=publish\n")

    def test_summer_and_winter_only_allow_their_own_cron(self):
        for month, expected, rejected in [
            (7, DAYLIGHT_CRON, STANDARD_CRON),
            (1, STANDARD_CRON, DAYLIGHT_CRON),
        ]:
            with self.subTest(month=month):
                now = datetime(2026, month, 15, 20, tzinfo=timezone.utc)
                self.assertTrue(allow_run("schedule", expected, now))
                self.assertFalse(allow_run("schedule", rejected, now))
                self.assertFalse(allow_run("schedule", "unexpected", now))

    def test_crons_map_to_three_requested_local_hours(self):
        for month, expression in [(7, DAYLIGHT_CRON), (1, STANDARD_CRON)]:
            with self.subTest(month=month):
                fields = expression.split()
                local_times = [
                    datetime(2026, month, 15, int(hour), int(fields[0]),
                             tzinfo=timezone.utc).astimezone(FORT_WAYNE)
                    for hour in fields[1].split(",")
                ]
                self.assertEqual(sorted(value.hour for value in local_times), [9, 13, 19])
                self.assertTrue(all(value.minute == 17 for value in local_times))

    def test_daylight_saving_transition_boundaries(self):
        for now, expected in [
            (datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc), STANDARD_CRON),
            (datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc), DAYLIGHT_CRON),
            (datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc), DAYLIGHT_CRON),
            (datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc), STANDARD_CRON),
        ]:
            with self.subTest(now=now):
                self.assertTrue(allow_run("schedule", expected, now))
                other = STANDARD_CRON if expected == DAYLIGHT_CRON else DAYLIGHT_CRON
                self.assertFalse(allow_run("schedule", other, now))

    def test_delayed_start_does_not_require_matching_current_hour(self):
        self.assertTrue(allow_run("schedule", DAYLIGHT_CRON,
                                  datetime(2026, 7, 15, 14, 42, tzinfo=timezone.utc)))

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            allow_run("schedule", DAYLIGHT_CRON, datetime(2026, 7, 15))


if __name__ == "__main__":
    unittest.main()
