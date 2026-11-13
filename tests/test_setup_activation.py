"""Actual saved-setup to post handoff, without publishing or billing side effects."""
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import meta_accounts
import public_setup


class SetupPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "AUTOPOST_WEB_DB": self.temp.name + "/preparation.db",
            "META_PAGES_ENCRYPTION_SECRET": "synthetic-setup-page-encryption-secret-long",
            "META_PAGE_ID": "", "META_PAGE_ACCESS_TOKEN": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        public_setup.init()
        meta_accounts.init()
        with public_setup.connect() as c:
            c.executescript("""
                CREATE TABLE app_posts(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,
                  platform TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL,
                  series_id INTEGER,frequency TEXT NOT NULL DEFAULT 'once',
                  page_id TEXT, workspace_id TEXT NOT NULL DEFAULT 'owner'
                );
                CREATE TABLE app_series(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,
                  platform TEXT NOT NULL,frequency TEXT NOT NULL,
                  time_zone TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
                  page_id TEXT,workspace_id TEXT NOT NULL DEFAULT 'owner'
                );
            """)
        self.now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        self.due = (self.now + timedelta(days=1)).isoformat()

    def setup(self, workspace="owner", **overrides):
        payload = {
            "setup_key": "1234567890abcdef1234567890abcdef",
            "platforms": ["facebook"], "message": "A fresh post from our own business.",
            "topic": "Friendly local updates", "source": "own_posts",
            "frequency": "once", "time_type": "at_time", "scheduled_at": self.due,
            "approval": "approve_every_post",
        }
        payload.update(overrides)
        public_setup.import_setup(payload, workspace)
        return public_setup.list_setups(workspace)[0]["id"]

    def connect_page(self, workspace="owner", page_id="12345"):
        meta_accounts.remember_pending(workspace, [{
            "page_id": page_id, "name": "Connected Fixture Page",
            "token": "synthetic-page-token", "tasks": ["CREATE_CONTENT"],
        }])
        meta_accounts.activate_pages(workspace, [page_id])

    def prepare(self, setup_id, values, workspace="owner", now=None):
        return public_setup.prepare_posts(setup_id, values, workspace, now or self.now)

    def test_connected_page_prepares_inactive_real_post_and_recurrence(self):
        setup_id = self.setup(frequency="daily")
        self.connect_page()
        result = self.prepare(setup_id, {"destination": "facebook_page", "page_id": "12345",
                                         "time_zone": "America/New_York"})
        self.assertEqual((result["status"], result["created"]), ("setup_only", True))
        with public_setup.connect() as c:
            post = c.execute("SELECT * FROM app_posts").fetchone()
            self.assertEqual((post["platform"], post["status"], post["page_id"]),
                             ("facebook_page", "setup_only", "12345"))
            self.assertIsNotNone(post["series_id"])
            self.assertEqual(c.execute("SELECT time_zone FROM app_series").fetchone()[0],
                             "America/New_York")
        self.assertEqual(public_setup.list_setups()[0]["prepared_post_id"], result["post_id"])

    def test_draft_is_real_and_repeated_completion_never_duplicates(self):
        setup_id = self.setup()
        values = {"destination": "draft"}
        first = self.prepare(setup_id, values)
        retry = self.prepare(setup_id, values, now=self.now + timedelta(hours=1))
        self.assertEqual(first["post_id"], retry["post_id"])
        self.assertEqual(retry["status"], "draft_ready")
        self.assertFalse(retry["created"])
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM app_series").fetchone()[0], 0)

    def test_draft_can_be_connected_and_revised_without_creating_duplicate_post(self):
        setup_id = self.setup()
        draft = self.prepare(setup_id, {"destination": "draft"})
        self.connect_page()
        selected = {"destination": "facebook_page", "page_id": "12345", "frequency": "daily"}
        converted = self.prepare(setup_id, selected)
        self.assertEqual(converted["post_id"], draft["post_id"])
        self.assertEqual(converted["status"], "setup_only")
        self.assertTrue(converted["updated"])
        self.prepare(setup_id, {**selected, "frequency": "weekly"})
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM app_series WHERE active=1").fetchone()[0], 1)
            post = c.execute("SELECT * FROM app_posts").fetchone()
            self.assertEqual((post["frequency"], post["status"]), ("weekly", "setup_only"))

    def test_already_activated_post_cannot_be_silently_retargeted(self):
        setup_id = self.setup()
        self.connect_page()
        selected = {"destination": "facebook_page", "page_id": "12345"}
        first = self.prepare(setup_id, selected)
        with public_setup.connect() as c:
            c.execute("UPDATE app_posts SET status='published' WHERE id=?", (first["post_id"],))
        retry = self.prepare(setup_id, selected)
        self.assertFalse(retry["created"])
        self.assertEqual(retry["status"], "published")
        with self.assertRaisesRegex(ValueError, "already been activated"):
            self.prepare(setup_id, {"destination": "draft"})

    def test_future_sources_and_unsupported_cadences_require_real_supported_choice(self):
        setup_id = self.setup(source="ai_drafts")
        with self.assertRaisesRegex(ValueError, "content source"):
            self.prepare(setup_id, {"destination": "draft"})
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 0)
        another = self.setup(setup_key="abcdef1234567890abcdef1234567890", frequency="twice_daily")
        with self.assertRaisesRegex(ValueError, "frequency"):
            self.prepare(another, {"destination": "draft"})
        self.assertEqual(self.prepare(another, {"destination": "draft", "frequency": "once"})["status"],
                         "draft_ready")

    def test_destination_and_workspace_isolation_before_any_post_is_created(self):
        setup_id = self.setup("customer-a")
        self.connect_page("customer-b")
        with self.assertRaisesRegex(ValueError, "not found"):
            self.prepare(setup_id, {"destination": "draft"}, "customer-b")
        with self.assertRaisesRegex(ValueError, "belonging to this account"):
            self.prepare(setup_id, {"destination": "facebook_page", "page_id": "12345"}, "customer-a")
        with patch.dict(os.environ, {"META_PAGE_ID": "owner-page", "META_PAGE_ACCESS_TOKEN": "owner-token"}):
            with self.assertRaisesRegex(ValueError, "own account"):
                self.prepare(setup_id, {"destination": "facebook"}, "customer-a")
        self.connect_page("customer-a", "56789")
        prepared = self.prepare(setup_id, {"destination": "facebook_page", "page_id": "56789"}, "customer-a")
        with public_setup.connect() as c:
            row = c.execute("SELECT workspace_id FROM app_posts WHERE id=?", (prepared["post_id"],)).fetchone()
            self.assertEqual(row[0], "customer-a")

    def test_invalid_time_zone_past_time_and_unavailable_destinations_do_not_write(self):
        setup_id = self.setup()
        self.connect_page()
        selected = {"destination": "facebook_page", "page_id": "12345"}
        for extra in [{"due_at": "2020-01-01T12:00:00+00:00"}, {"due_at": "2026-11-16T12:00:00"},
                      {"time_zone": "Fake/Location"}, {"destination": "youtube"}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.prepare(setup_id, {**selected, **extra})
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 0)

    def test_parallel_retries_prepare_one_post(self):
        setup_id = self.setup()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.prepare(setup_id, {"destination": "draft"}), range(4)))
        self.assertEqual(len({r["post_id"] for r in results}), 1)
        self.assertEqual(sum(r["created"] for r in results), 1)

    def test_multiple_selected_pages_create_one_post_each_and_retry_in_any_order(self):
        setup_id = self.setup("customer-a", frequency="weekly")
        self.connect_page("customer-a", "12345")
        self.connect_page("customer-a", "56789")
        values = {"destination": "facebook_page", "page_ids": ["56789", "12345"]}
        created = self.prepare(setup_id, values, "customer-a")
        self.assertEqual(len(created["post_ids"]), 2)
        self.assertEqual(created["created_count"], 2)
        self.assertEqual({p["page_id"] for p in created["posts"]}, {"12345", "56789"})
        retried = self.prepare(setup_id, {**values, "page_ids": ["12345", "56789"]}, "customer-a")
        self.assertEqual(retried["post_ids"], created["post_ids"])
        self.assertFalse(retried["created"])
        self.assertEqual(public_setup.list_setups("customer-a")[0]["prepared_post_ids"], created["post_ids"])
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM app_series WHERE active=1").fetchone()[0], 2)

    def test_multi_page_selection_rejects_foreign_page_atomically(self):
        setup_id = self.setup("customer-a")
        self.connect_page("customer-a", "12345")
        self.connect_page("customer-b", "56789")
        with self.assertRaisesRegex(ValueError, "belonging to this account"):
            self.prepare(setup_id, {"destination": "facebook_page", "page_ids": ["12345", "56789"]}, "customer-a")
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM app_posts").fetchone()[0], 0)
        for page_ids in [[], ["12345", "12345"], "12345"]:
            with self.subTest(page_ids=page_ids), self.assertRaises(ValueError):
                self.prepare(setup_id, {"destination": "facebook_page", "page_ids": page_ids}, "customer-a")

    def test_selected_pages_can_change_before_activation_without_duplicate_active_posts(self):
        setup_id = self.setup(frequency="daily")
        self.connect_page(page_id="12345")
        self.connect_page(page_id="56789")
        values = {"destination": "facebook_page", "page_ids": ["12345", "56789"]}
        first = self.prepare(setup_id, values)
        keep = next(p for p in first["posts"] if p["page_id"] == "56789")
        removed = next(p for p in first["posts"] if p["page_id"] == "12345")
        revised = self.prepare(setup_id, {**values, "page_ids": ["56789"]})
        self.assertEqual(revised["post_ids"], [keep["id"]])
        with public_setup.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM app_posts WHERE id=?", (removed["id"],)).fetchone()[0], "cancelled")
            self.assertEqual(c.execute("SELECT count(*) FROM app_series WHERE active=1").fetchone()[0], 1)
            c.execute("UPDATE app_posts SET status='scheduled' WHERE id=?", (keep["id"],))
        with self.assertRaisesRegex(ValueError, "already been activated"):
            self.prepare(setup_id, values)

    def test_customer_preparation_refuses_legacy_schema_without_workspace_columns(self):
        setup_id = self.setup("customer-a")
        with public_setup.connect() as c:
            c.execute("ALTER TABLE app_posts DROP COLUMN workspace_id")
            c.execute("ALTER TABLE app_series DROP COLUMN workspace_id")
        with self.assertRaisesRegex(ValueError, "workspace scheduling"):
            self.prepare(setup_id, {"destination": "draft"}, "customer-a")


if __name__ == "__main__":
    unittest.main()
