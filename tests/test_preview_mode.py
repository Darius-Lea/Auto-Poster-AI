"""Free hosted preview must not publish, solicit payment, or show fake live features."""
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch


class PreviewSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.settings = patch.dict(os.environ, {
            "AUTOPOST_APP_PASSWORD": "safe-test-password-abcdefgh",
            "AUTOPOST_WEB_SECRET": "safe-test-secret-longer-than-thirty-two-chars",
            "AUTOPOST_WEB_DB": cls.temp.name + "/preview.db",
            "AUTOPOST_REQUIRE_PAYMENT": "false",
            "AUTOPOST_PREVIEW_MODE": "true",
            "AUTOPOST_PUBLIC_LANDING": "false",
            "META_PAGE_ID": "",
            "META_PAGE_ACCESS_TOKEN": "",
        })
        cls.settings.start()
        import app_mvp
        cls.web = importlib.reload(app_mvp)
        cls.web.init_db()
        cls.web.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.settings.stop()
        cls.temp.cleanup()

    def setUp(self):
        with self.web.db() as c:
            c.execute("DELETE FROM app_posts")
            c.execute("DELETE FROM app_series")

    def _login(self, c):
        self.assertEqual(c.post("/login", data={
            "password": "safe-test-password-abcdefgh"
        }).status_code, 302)
        with c.session_transaction() as sess:
            return sess["csrf"]

    def test_dashboard_explains_test_mode_and_has_no_default_destination(self):
        with self.web.app.test_client() as c:
            self._login(c)
            response=c.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Free beta: safe preview mode", response.data)
            self.assertIn(b"Review post", response.data)
            self.assertNotIn(b'name="destinations" value="draft" checked', response.data)
            self.assertIn(b"Coming later", response.data)

    def test_draft_scheduling_allowed(self):
        with self.web.app.test_client() as c:
            csrf=self._login(c)
            response=c.post("/schedule",data={
                "_csrf":csrf,"content":"Test post to a draft",
                "destinations":"draft","when":"now","time_zone":"UTC"
            })
            self.assertEqual(response.status_code,302)
        with self.web.db() as connection:
            row=connection.execute("SELECT platform,status FROM app_posts").fetchone()
        self.assertEqual((row["platform"],row["status"]),("draft","scheduled"))

    def test_social_posting_rejected_even_when_connection_claimed(self):
        with self.web.app.test_client() as c:
            csrf=self._login(c)
            r=c.post("/schedule",data={
                "_csrf":csrf,"content":"No external post",
                "destinations":"facebook","when":"now","time_zone":"UTC"
            })
            self.assertEqual(r.status_code,403)
        with self.web.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM app_posts").fetchone()[0],0)

    def test_legacy_queue_external_post_held_not_sent(self):
        with self.web.db() as connection:
            connection.execute(
                "INSERT INTO app_posts(content,platform,due_at,status) VALUES(?,?,?,'scheduled')",
                ("Do not publish","facebook",
                (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
            )
        with patch.object(self.web,"publish_to_facebook",side_effect=AssertionError("called")):
            self.assertEqual(self.web.run_due(),1)
        with self.web.db() as connection:
            row=connection.execute("SELECT status FROM app_posts").fetchone()
            self.assertEqual(row["status"],"needs_connection")

    def test_checkout_and_website_monitoring_blocked(self):
        with self.web.app.test_client() as c:
            csrf=self._login(c)
            self.assertEqual(c.get("/control").status_code,200)
            self.assertIn(b"Free beta preview",c.get("/control").data)
            self.assertEqual(c.post("/billing/checkout",data={"_csrf":csrf}).status_code,403)
            self.assertEqual(c.post("/website",data={
                "_csrf":csrf,"url":"https://example.com/rss","cadence":"daily"
            }).status_code,403)

    def test_social_login_disabled_on_free_preview(self):
        with self.web.app.test_client() as c:
            self._login(c)
            self.assertEqual(c.get("/facebook/connect").status_code,403)


if __name__=="__main__":
    unittest.main()
