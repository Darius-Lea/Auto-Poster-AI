"""Password-free visitor onboarding: never authorize posting before account setup."""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def example():
    return {
        "setup_key": "0123456789abcdef0123456789abcdef",
        "platforms": ["facebook", "instagram", "tiktok"],
        "message": "We are open this weekend. Visit our shop!",
        "topic": "Friendly announcements and verified information.",
        "source": "own_posts",
        "frequency": "twice_daily",
        "time_type": "at_time",
        "scheduled_at": (datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
        "approval": "approve_every_post",
    }


class PublicOnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.settings = patch.dict(os.environ, {
            "AUTOPOST_WEB_DB": cls.temp.name + "/guest.db",
            "AUTOPOST_APP_PASSWORD": "long-test-private-beta-password",
            "AUTOPOST_WEB_SECRET": "a-unique-test-web-secret-longer-than-thirty-two",
            "AUTOPOST_REQUIRE_PAYMENT": "true",
            "AUTOPOST_PREVIEW_MODE": "true",
            "AUTOPOST_PUBLIC_LANDING": "true",
        })
        cls.settings.start()
        import app_mvp
        cls.web = importlib.reload(app_mvp)
        cls.web.init_db()
        cls.web.app.config["TESTING"] = True
        import public_setup
        cls.storage = public_setup

    @classmethod
    def tearDownClass(cls):
        cls.settings.stop()
        cls.temp.cleanup()

    def setUp(self):
        with self.storage.connect() as db:
            db.execute("DELETE FROM imported_guest_setups")
        with self.web.db() as db:
            db.execute("DELETE FROM app_posts")
            db.execute("DELETE FROM app_series")

    def owner_login(self, client):
        response = client.post("/login", data={"password": "long-test-private-beta-password"})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as sess:
            return sess["csrf"]

    def test_public_site_points_to_wizard_not_password(self):
        with self.web.app.test_client() as client:
            site = client.get("/")
            self.assertEqual(site.status_code, 302)
            homepage = client.get("/welcome")
            self.assertEqual(homepage.status_code, 200)
            self.assertIn(b'href="/start"', homepage.data)
            self.assertIn(b"Start for free", homepage.data)

    def test_no_password_required_to_choose_content_platforms_and_schedule(self):
        with self.web.app.test_client() as client:
            result = client.get("/start")
            self.assertEqual(result.status_code, 200)
            html = result.get_data(as_text=True)
            self.assertIn('name="platforms"', html)
            self.assertIn('id="message"', html)
            self.assertIn('id="frequency"', html)
            self.assertIn('id="scheduled_at"', html)
            self.assertIn('data-step="4"', html)
            self.assertIn("No account, payment, or external publishing yet", html)
            self.assertNotIn('action="/schedule"', html)
            self.assertNotIn('action="/start/import"', html)
            self.assertNotIn('type="password"', html)
        self.assertEqual(self.storage.list_setups(), [])

    def test_guest_import_requires_login_and_csrf(self):
        payload = json.dumps(example())
        with self.web.app.test_client() as client:
            self.assertEqual(client.post("/start/import", data={
                "setup_payload": payload
            }).status_code, 401)
            csrf = self.owner_login(client)
            self.assertEqual(client.post("/start/import", data={
                "setup_payload": payload
            }).status_code, 403)
            self.assertEqual(client.post("/start/import", data={
                "_csrf": csrf, "setup_payload": payload
            }).status_code, 302)
        self.assertEqual(len(self.storage.list_setups()), 1)
        with self.web.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM app_posts").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM app_series").fetchone()[0], 0)

    def test_imported_setup_visible_in_dashboard_and_always_inactive(self):
        self.storage.import_setup(example())
        with self.web.app.test_client() as client:
            self.owner_login(client)
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn(b"Prepared setups", home.data)
            self.assertIn(b"We are open this weekend", home.data)
            self.assertIn(b"Not active", home.data)
        import app_control
        self.assertFalse(app_control.allowed())
        self.assertEqual(self.web.run_due(), 0)

    def test_import_is_idempotent(self):
        self.assertTrue(self.storage.import_setup(example()))
        self.assertFalse(self.storage.import_setup(example()))
        self.assertEqual(len(self.storage.list_setups()), 1)

    def test_invalid_guest_inputs_never_write(self):
        invalids = [
            {**example(), "platforms": []},
            {**example(), "platforms": ["facebook", "facebook"]},
            {**example(), "platforms": [{"hack": "data"}]},
            {**example(), "message": ""},
            {**example(), "frequency": "every_minute"},
            {**example(), "time_type": "at_time", "scheduled_at": "soon"},
            {**example(), "setup_key": "../../bad"},
        ]
        for payload in invalids:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.storage.import_setup(payload)
        self.assertEqual(self.storage.list_setups(), [])

    def test_user_input_is_escaped_when_imported(self):
        record = example()
        record["message"] = "<script>alert('hi')</script>"
        self.storage.import_setup(record)
        with self.web.app.test_client() as client:
            self.owner_login(client)
            data = client.get("/").get_data(as_text=True)
            self.assertIn("&lt;script&gt;", data)
            self.assertNotIn("<script>alert('hi')</script>", data)


if __name__ == "__main__":
    unittest.main()
