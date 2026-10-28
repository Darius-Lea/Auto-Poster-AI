from datetime import datetime, timezone
import unittest

from core.posting_schedule import (
    DAYLIGHT_CRON, STANDARD_CRON, FORT_WAYNE, allow_run,
)


class PostingScheduleTests(unittest.TestCase):
    def test_manual_runs_are_allowed_without_a_cron(self):
        self.assertTrue(allow_run("workflow_dispatch", ""))

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
