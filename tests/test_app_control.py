"""Offline regression checks for the mobile control center and paid publishing gate."""
import os
import tempfile
import unittest
from unittest.mock import patch

import app_control


def stripe_event(status="active", end=9999999999, created=123):
    return {
        "type": "customer.subscription.updated", "created": created,
        "data": {"object": {
            "id": "sub_test_control", "customer": "cus_test_control",
            "status": status, "current_period_end": end,
            "metadata": {"workspace_id": "owner"},
        }},
    }


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "AUTOPOST_WEB_DB": self.tempdir.name + "/control.db",
            "AUTOPOST_REQUIRE_PAYMENT": "true",
        })
        self.env.start()
        app_control.init()

    def tearDown(self):
        self.env.stop()
        self.tempdir.cleanup()

    def test_unpaid_blocks_real_jobs(self):
        app_control.update_settings({"enabled": 1, "booking_enabled": 1})
        result = app_control.claim_run("publish", 1700000000)
        self.assertFalse(result["run"])
        self.assertIn("subscription", result["reason"].lower())

    def test_verified_subscription_enables_one_interval(self):
        app_control.update_settings({"enabled": 1, "booking_enabled": 1,
                                     "booking_interval_minutes": 60})
        self.assertTrue(app_control.record_subscription_event(stripe_event()))
        self.assertTrue(app_control.allowed(1700000000))
        self.assertTrue(app_control.claim_run("publish", 1700000000)["run"])
        self.assertFalse(app_control.claim_run("publish", 1700000120)["run"])
        self.assertTrue(app_control.claim_run("publish", 1700003600)["run"])

    def test_payment_failure_pauses_publishing(self):
        app_control.update_settings({"enabled": 1})
        app_control.record_subscription_event(stripe_event(created=200))
        app_control.record_subscription_event(stripe_event(
            status="past_due", created=201
        ))
        self.assertFalse(app_control.allowed(1700000000))
        self.assertFalse(app_control.claim_run("publish", 1700000000)["run"])
        # Older webhook can't reinstate an overdue subscription.
        app_control.record_subscription_event(stripe_event(created=199))
        self.assertFalse(app_control.allowed(1700000000))

    def test_expiration_pauses_even_with_active_status(self):
        app_control.record_subscription_event(stripe_event(end=1700000000))
        self.assertFalse(app_control.allowed(1700000001))

    def test_controls_are_not_billing_fields(self):
        with self.assertRaises(ValueError):
            app_control.update_settings({"paid": 1})
        with self.assertRaises(ValueError):
            app_control.update_settings({"max_posts_per_run": 999})

    def test_run_mode_honors_disabled_status_checks(self):
        app_control.record_subscription_event(stripe_event())
        app_control.update_settings({"enabled": 1, "booking_enabled": 1,
                                     "status_enabled": 0})
        r = app_control.claim_run("publish-and-check", 1700000000)
        self.assertTrue(r["run"])
        self.assertEqual(r["mode"], "publish")

    def test_booking_pause_allows_only_status_check(self):
        app_control.record_subscription_event(stripe_event())
        app_control.update_settings({"enabled": 1, "booking_enabled": 0,
                                     "status_enabled": 1})
        r = app_control.claim_run("publish-and-check", 1700000000)
        self.assertTrue(r["run"])
        self.assertEqual(r["mode"], "check-status")

    def test_unverified_source_defaults_off(self):
        app_control.record_subscription_event(stripe_event())
        app_control.update_settings({"enabled": 1})
        self.assertFalse(app_control.claim_run("publish-wanted", 1700000000)["run"])
        self.assertFalse(app_control.claim_run("publish-case", 1700000000)["run"])

    def test_webhook_for_other_workspace_is_ignored(self):
        e = stripe_event()
        e["data"]["object"]["metadata"]["workspace_id"] = "attacker"
        self.assertFalse(app_control.record_subscription_event(e))
        self.assertFalse(app_control.allowed(1700000000))


if __name__ == "__main__":
    unittest.main()
