"""Offline sanity tests for the owner-operated web prototype."""
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class WebMVPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "test.db")
        cls.env = patch.dict(os.environ, {
            "AUTOPOST_APP_PASSWORD": "a-long-test-password-123",
            "AUTOPOST_WEB_SECRET": "test-only-very-long-secret-key-for-cookies",
            "AUTOPOST_WEB_DB": cls.db_path
        })
        cls.env.start()
        import app_mvp
        cls.app = importlib.reload(app_mvp)
        cls.app.init_db()
        cls.app.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        # Each test gets a clean queue to avoid jobs from earlier tests.
        with self.app.db() as c:
            c.execute("DELETE FROM app_posts")
            c.execute("DELETE FROM app_series")

    def login(self, client):
        r = client.post("/login", data={"password": "a-long-test-password-123"})
        self.assertEqual(r.status_code, 302)
        with client.session_transaction() as sess:
            return sess["csrf"]

    def test_login_required(self):
        with self.app.app.test_client() as client:
            self.assertEqual(client.get("/").status_code, 401)

    def test_schedule_draft_and_run(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            r = client.post("/schedule", data={
                "_csrf": csrf, "content": "A creator's draft post",
                "destinations": "draft", "when": "later", "utc_due": due
            })
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.app.run_due(), 1)
            with self.app.db() as c:
                row = c.execute("SELECT status FROM app_posts ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["status"], "draft_ready")

    def test_csrf_required(self):
        with self.app.app.test_client() as client:
            self.login(client)
            r = client.post("/schedule", data={
                "content": "Hello", "platform": "draft",
                "utc_due": datetime.now(timezone.utc).isoformat()
            })
            self.assertEqual(r.status_code, 403)

    def test_no_fb_without_page_connection(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            with patch.dict(os.environ, {"META_PAGE_ID": "", "META_PAGE_ACCESS_TOKEN": ""}):
                r = client.post("/schedule", data={
                    "_csrf": csrf, "content": "Hello", "destinations": "facebook",
                    "when": "now", "frequency": "once", "time_zone": "UTC"
                })
            self.assertEqual(r.status_code, 302)
            with self.app.db() as c:
                row = c.execute("SELECT status FROM app_posts ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["status"], "needs_connection")

    def test_post_to_multiple_destinations(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            with patch.dict(os.environ, {"META_PAGE_ID": "", "META_PAGE_ACCESS_TOKEN": ""}):
                r = client.post("/schedule", data={
                    "_csrf": csrf, "content": "One post, several apps",
                    "destinations": ["draft", "instagram", "facebook"],
                    "when": "now", "frequency": "once", "time_zone": "UTC",
                })
            self.assertEqual(r.status_code, 302)
        with self.app.db() as c:
            rows = c.execute(
                "SELECT platform,status FROM app_posts WHERE content=?",
                ("One post, several apps",),
            ).fetchall()
        self.assertEqual({r["platform"]:r["status"] for r in rows}, {
            "draft": "scheduled", "instagram": "needs_connection",
            "facebook": "needs_connection",
        })

    def test_custom_websites_never_pretend_to_publish(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            r = client.post("/schedule", data={
                "_csrf": csrf, "content": "Some relevant update",
                "websites": ["https://example.org/publisher", "https://example.net"],
                "when": "now", "frequency": "daily", "time_zone": "UTC",
            })
            self.assertEqual(r.status_code, 302)
        with self.app.db() as c:
            rows = c.execute(
                "SELECT status,website_url,series_id FROM app_posts "
                "WHERE content=? ORDER BY id", ("Some relevant update",),
            ).fetchall()
        self.assertEqual([r["status"] for r in rows],
                         ["needs_connection", "needs_connection"])
        self.assertTrue(all(r["series_id"] is None for r in rows))
        self.assertEqual(rows[0]["website_url"], "https://example.org/publisher")

    def test_invalid_website_rejected(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            r = client.post("/schedule", data={
                "_csrf": csrf, "content": "Hello", "websites": "http://example.net",
                "when": "now", "frequency": "once", "time_zone": "UTC",
            })
            self.assertEqual(r.status_code, 400)

    def test_daily_repeat_and_stop(self):
        with self.app.app.test_client() as client:
            csrf = self.login(client)
            now = datetime.now(timezone.utc)
            r = client.post("/schedule", data={
                "_csrf": csrf, "content": "My recurring draft",
                "destinations": "draft", "frequency": "daily",
                "when": "now", "time_zone": "America/Indiana/Indianapolis",
            })
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.app.run_due(), 1)
            with self.app.db() as c:
                rows = c.execute(
                    "SELECT id,status,series_id,due_at FROM app_posts "
                    "WHERE content='My recurring draft' ORDER BY id"
                ).fetchall()
            self.assertEqual([r["status"] for r in rows], ["draft_ready", "scheduled"])
            self.assertIsNotNone(rows[1]["series_id"])
            self.assertGreater(datetime.fromisoformat(rows[1]["due_at"]), now)
            r = client.post("/cancel/" + str(rows[1]["id"]), data={"_csrf": csrf})
            self.assertEqual(r.status_code, 302)
            with self.app.db() as c:
                active = c.execute("SELECT active FROM app_series WHERE id=?",
                                   (rows[1]["series_id"],)).fetchone()["active"]
                status = c.execute("SELECT status FROM app_posts WHERE id=?",
                                   (rows[1]["id"],)).fetchone()["status"]
            self.assertEqual(active, 0)
            self.assertEqual(status, "cancelled")

    def test_uncertain_fb_is_held(self):
        with self.app.db() as c:
            c.execute(
                "INSERT INTO app_posts(content,platform,due_at,status) "
                "VALUES(?,?,?,'scheduled')",
                ("Hello Facebook", "facebook",
                 (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
            )
        with patch.object(self.app, "publish_to_facebook", side_effect=TimeoutError("unclear")):
            self.assertEqual(self.app.run_due(), 1)
        with self.app.db() as c:
            row = c.execute("SELECT status FROM app_posts ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
