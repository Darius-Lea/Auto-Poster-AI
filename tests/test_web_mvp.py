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
                "platform": "draft", "utc_due": due
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
                    "_csrf": csrf, "content": "Hello", "platform": "facebook",
                    "utc_due": datetime.now(timezone.utc).isoformat()
                })
            self.assertEqual(r.status_code, 400)

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
