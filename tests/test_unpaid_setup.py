"""Unpaid signup/setup is supported but delivery requires paid explicit activation."""
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class UnpaidSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "AUTOPOST_WEB_DB": cls.tmp.name + "/unpaid.db",
            "AUTOPOST_APP_PASSWORD": "test-setup-password-at-least-12",
            "AUTOPOST_WEB_SECRET": "unique-long-separate-test-secret-for-this-session",
            "AUTOPOST_REQUIRE_PAYMENT": "true",
            "AUTOPOST_PREVIEW_MODE": "false",
            "AUTOPOST_PUBLIC_LANDING": "false",
            "META_PAGE_ID": "",
            "META_PAGE_ACCESS_TOKEN": "",
        })
        cls.env.start()
        import app_mvp
        cls.app = importlib.reload(app_mvp)
        cls.app.init_db()
        cls.app.app.config["TESTING"] = True
        import automation_setup
        cls.automations = automation_setup

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        with self.app.db() as c:
            c.execute("DELETE FROM app_posts")
            c.execute("DELETE FROM app_series")
        with self.automations.connect() as c:
            c.execute("DELETE FROM automation_setup_plans")
            c.execute("DELETE FROM subscriptions")

    def login(self, client):
        self.assertEqual(client.post("/login", data={
            "password": "test-setup-password-at-least-12"
        }).status_code, 302)
        with client.session_transaction() as sess:
            return sess["_csrf"] if "_csrf" in sess else sess["csrf"]

    def test_free_user_can_set_up_facebook_post_without_connection_or_payment(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            response = client.post("/schedule", data={
                "_csrf": csrf, "content": "Our next product update",
                "destinations": "facebook", "when": "now",
                "frequency": "daily", "time_zone": "UTC",
            })
            self.assertEqual(response.status_code, 302)
        with self.app.db() as c:
            row = c.execute("SELECT status,series_id FROM app_posts").fetchone()
        self.assertEqual(row["status"], "setup_only")
        self.assertIsNotNone(row["series_id"])
        with patch.object(self.app, "publish_to_facebook",
                          side_effect=AssertionError("unpaid attempted posting")):
            self.assertEqual(self.app.run_due(), 0)

    def test_customer_payment_never_auto_activates_the_planned_post(self):
        import app_control
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            self.assertEqual(client.post("/schedule", data={
                "_csrf": csrf, "content": "Hold until I activate",
                "destinations": "facebook", "when": "later", "utc_due": due
            }).status_code, 302)
        with self.app.db() as c:
            row = c.execute("SELECT id,status FROM app_posts").fetchone()
            post_id=row["id"]
            self.assertEqual(row["status"], "setup_only")
        event = {
            "type": "customer.subscription.updated",
            "created": 123456,
            "data": {"object": {
                "id": "sub_unpaid_setup", "customer": "cus_unpaid_setup",
                "status": "active", "current_period_end": 9999999999,
                "metadata": {"workspace_id": "owner"}
            }}
        }
        self.assertTrue(app_control.record_subscription_event(event))
        with self.app.db() as c:
            self.assertEqual(c.execute("SELECT status FROM app_posts").fetchone()[0],
                             "setup_only")
        with patch.object(self.app, "publish_to_facebook",
                          side_effect=AssertionError("automatically published")):
            self.assertEqual(self.app.run_due(now=datetime.now(timezone.utc) + timedelta(days=2)), 0)
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            # Paid but no Page connection cannot activate.
            self.assertEqual(client.post(f"/activate/{post_id}",data={
                "_csrf": csrf
            }).status_code, 409)

    def test_unpaid_drafts_can_complete_without_payment(self):
        with self.app.app.test_client() as client:
            csrf=self.login(client)
            r=client.post("/schedule",data={
                "_csrf":csrf,"content":"Internal content draft",
                "destinations":"draft","when":"now"
            })
            self.assertEqual(r.status_code,302)
        self.assertEqual(self.app.run_due(),1)
        with self.app.db() as c:
            status=c.execute("SELECT status FROM app_posts").fetchone()[0]
        self.assertEqual(status,"draft_ready")

    def test_unpaid_automation_plan_saves_and_edits_without_worker(self):
        with self.app.app.test_client() as client:
            csrf=self.login(client)
            values={
                "_csrf":csrf,"name":"Business offers","topic":"Daily specials",
                "kind":"manual","destination":"facebook_later",
                "cadence":"daily","review_mode":"approve_first",
            }
            self.assertEqual(client.post("/automations", data=values).status_code,302)
            plans=self.automations.list_plans()
            self.assertEqual(len(plans),1)
            self.assertEqual(plans[0]["status"],"setup_only")
            values.update({"plan_id":str(plans[0]["id"]),"cadence":"twice_daily"})
            self.assertEqual(client.post("/automations", data=values).status_code,302)
            self.assertEqual(self.automations.list_plans()[0]["cadence"],"twice_daily")
        with self.app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM app_posts").fetchone()[0],0)

    def test_automation_plan_requires_valid_topic_and_safe_source_url(self):
        from automation_setup import validate
        values={
            "name":"Test plan","topic":"Website updates",
            "kind":"website","destination":"facebook_later",
            "cadence":"weekly","review_mode":"approve_first",
            "source_url":"https://example.org/rss",
        }
        self.assertEqual(validate(values)["source_url"],"https://example.org/rss")
        for url in ["http://example.org/rss", "https://localhost/private",
                    "https://user:secret@example.com/rss", "https://example.com:abc/rss"]:
            with self.assertRaises(ValueError):
                validate({**values,"source_url":url})

    def test_discard_setup_without_payment(self):
        with self.app.app.test_client() as client:
            csrf=self.login(client)
            client.post("/schedule", data={
                "_csrf":csrf, "content":"Cancel my plan",
                "destinations":"facebook", "when":"now", "time_zone":"UTC"
            })
            with self.app.db() as c:
                row=c.execute("SELECT id FROM app_posts").fetchone()
            self.assertEqual(client.post(f"/cancel/{row['id']}",data={
                "_csrf":csrf
            }).status_code,302)
        with self.app.db() as c:
            self.assertEqual(c.execute("SELECT status FROM app_posts").fetchone()[0],
                             "cancelled")


if __name__ == "__main__":
    unittest.main()
