"""Safe, offline tests for no-developer-key multi-Page onboarding and routing."""
from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def example(page_id="12345678", name="Fort Wayne Gone Wild", publish=True):
    return {
        "page_id": page_id, "name": name,
        "token": "EAA-Fake-test-secret-for-" + page_id,
        "tasks": ["CREATE_CONTENT", "ANALYZE"] if publish else ["ANALYZE"],
    }


class PageStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import meta_accounts
        cls.accounts = meta_accounts

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "AUTOPOST_WEB_DB": self.tmp.name + "/pages.db",
            "META_PAGES_ENCRYPTION_SECRET": "non-production-dev-only-encryption-key-is-long",
        })
        self.env.start()
        self.accounts.init()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_one_customer_login_connects_separate_pages(self):
        self.accounts.remember_pending("customer-a", [
            example("123", "News Page"), example("456", "Food Page")
        ])
        self.assertEqual(len(self.accounts.pending_choices("customer-a")), 2)
        self.assertEqual(self.accounts.activate_pages("customer-a", ["123", "456"]), 2)
        pages = self.accounts.list_pages("customer-a")
        self.assertEqual({p["page_id"] for p in pages}, {"123", "456"})
        self.assertNotIn("token", str(pages))
        self.assertEqual(
            self.accounts.get_page_token("customer-a", "123"),
            example("123", "News Page")["token"]
        )

    def test_tokens_are_encrypted_not_readable_in_database(self):
        self.accounts.remember_pending("owner", [example("123")])
        self.accounts.activate_pages("owner", ["123"])
        with self.accounts._db() as db:
            cipher_text = db.execute(
                "SELECT encrypted_access_token FROM connected_facebook_pages"
            ).fetchone()[0]
        self.assertNotIn("EAA-Fake", cipher_text)

    def test_workspace_isolation_and_unauthorized_page_rejected(self):
        self.accounts.remember_pending("customer-a", [example("123")])
        self.accounts.activate_pages("customer-a", ["123"])
        self.assertEqual(self.accounts.list_pages("customer-b"), [])
        with self.assertRaises(PermissionError):
            self.accounts.get_page_token("customer-b", "123")
        with self.assertRaises(PermissionError):
            self.accounts.activate_pages("customer-b", ["123"])

    def test_no_implicit_publish_on_noncreator_page(self):
        self.accounts.remember_pending("owner", [example("234", publish=False)])
        self.assertFalse(self.accounts.pending_choices("owner")[0]["can_publish"])
        with self.assertRaises(PermissionError):
            self.accounts.activate_pages("owner", ["234"])

    def test_expired_page_choice_cannot_activate(self):
        self.accounts.remember_pending("owner", [example("123")], now=1)
        with self.assertRaises(PermissionError):
            self.accounts.activate_pages("owner", ["123"], now=9000)

    def test_disconnecting_page_does_not_remove_other_page(self):
        self.accounts.remember_pending("owner", [example("123"), example("456")])
        self.accounts.activate_pages("owner", ["123", "456"])
        self.assertTrue(self.accounts.disconnect("owner", "123"))
        self.assertEqual(
            [p["page_id"] for p in self.accounts.list_pages("owner")], ["456"]
        )

    def test_missing_config_raises_without_any_user_developer_key(self):
        with patch.dict(os.environ, {
            "META_APP_ID": "", "META_APP_SECRET": "", "META_OAUTH_REDIRECT_URI": ""
        }):
            self.assertFalse(self.accounts.configured())
            with self.assertRaises(RuntimeError):
                self.accounts.login_url("a" * 60)

    def test_login_url_contains_one_provider_app_not_page_secrets(self):
        with patch.dict(os.environ, {
            "META_APP_ID": "9999", "META_APP_SECRET": "server-secret",
            "META_OAUTH_REDIRECT_URI": "https://app.example.com/facebook/callback"
        }):
            url = self.accounts.login_url("a" * 40)
            self.assertIn("client_id=9999", url)
            self.assertIn("pages_manage_posts", url)
            self.assertNotIn("server-secret", url)
            self.assertNotIn("page_access_token", url)


class RealPageSchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            "AUTOPOST_APP_PASSWORD": "test-password-123456",
            "AUTOPOST_WEB_SECRET": "long-random-test-web-secret-key-for-session",
            "AUTOPOST_WEB_DB": cls.tmp.name + "/app.db",
            "META_PAGES_ENCRYPTION_SECRET": "long-test-encryption-secret-do-not-use",
            "AUTOPOST_REQUIRE_PAYMENT": "false",
        })
        cls.env.start()
        import app_mvp
        cls.app = importlib.reload(app_mvp)
        cls.app.init_db()
        cls.app.app.config["TESTING"] = True
        import meta_accounts
        cls.accounts = meta_accounts

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        with self.app.db() as c:
            c.execute("DELETE FROM app_posts")
            c.execute("DELETE FROM app_series")
        with self.accounts._db() as c:
            c.execute("DELETE FROM pending_facebook_pages")
            c.execute("DELETE FROM connected_facebook_pages")
        self.accounts.remember_pending("owner", [
            example("123", "Local news"), example("456", "My shop")
        ])
        self.accounts.activate_pages("owner", ["123", "456"])

    def login(self, client):
        response = client.post("/login", data={"password": "test-password-123456"})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as sess:
            return sess["csrf"]

    def test_destination_list_shows_both_business_pages(self):
        with self.app.app.test_client() as client:
            self.login(client)
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Local news", response.data)
            self.assertIn(b"My shop", response.data)

    def test_post_to_one_specific_business_page(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            result = client.post("/schedule", data={
                "_csrf": csrf, "content": "Today's sales",
                "facebook_pages": "456", "when": "now", "time_zone": "UTC"
            })
            self.assertEqual(result.status_code, 302)
        with patch.object(self.accounts, "publish_message",
                          return_value="meta:456_9999") as publisher:
            self.assertEqual(self.app.run_due(), 1)
            publisher.assert_called_once_with("owner", "456", "Today's sales")
        with self.app.db() as c:
            row = c.execute("SELECT page_id,status,remote_id FROM app_posts").fetchone()
        self.assertEqual((row["page_id"], row["status"], row["remote_id"]),
                         ("456", "published", "meta:456_9999"))

    def test_post_to_two_pages_creates_separate_deliveries(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            result = client.post("/schedule", data={
                "_csrf": csrf, "content": "Hello",
                "facebook_pages": ["123", "456"], "when": "now", "time_zone": "UTC"
            })
            self.assertEqual(result.status_code, 302)
        with patch.object(self.accounts, "publish_message",
                          side_effect=lambda ws, page, txt: "meta:" + page) as pub:
            self.assertEqual(self.app.run_due(), 2)
            self.assertEqual({c.args[1] for c in pub.call_args_list}, {"123", "456"})
        with self.app.db() as c:
            rows = c.execute("SELECT page_id,status FROM app_posts").fetchall()
        self.assertEqual({x["page_id"] for x in rows}, {"123", "456"})
        self.assertTrue(all(x["status"] == "published" for x in rows))

    def test_forged_page_id_cannot_be_scheduled(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            r = client.post("/schedule", data={
                "_csrf": csrf, "content": "Fake", "facebook_pages": "99999",
                "when": "now", "time_zone": "UTC"
            })
            self.assertEqual(r.status_code, 400)
        with self.app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM app_posts").fetchone()[0], 0)

    def test_disconnecting_page_cancels_queued_posts_and_recurring_series(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            client.post("/schedule", data={
                "_csrf": csrf, "content": "Never send after revoke",
                "facebook_pages": "123", "when": "now",
                "frequency": "daily", "time_zone": "UTC"
            })
            r = client.post("/facebook/disconnect/123", data={"_csrf": csrf})
            self.assertEqual(r.status_code, 302)
        with self.app.db() as c:
            row = c.execute("SELECT status FROM app_posts WHERE page_id='123'").fetchone()
            series = c.execute("SELECT active FROM app_series WHERE page_id='123'").fetchone()
        self.assertEqual(row["status"], "needs_connection")
        self.assertEqual(series["active"], 0)


if __name__ == "__main__":
    unittest.main()
