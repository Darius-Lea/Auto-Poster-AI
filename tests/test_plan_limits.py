"""Customer Basic posting limits are enforced at configuration and delivery time."""
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app_control
import plan_limits


class PlanLimitsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"AUTOPOST_WEB_DB": self.directory.name + "/plans.db"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        app_control.init()
        plan_limits.init()
        with self.db() as connection:
            connection.execute("""CREATE TABLE app_posts(
                id INTEGER PRIMARY KEY,workspace_id TEXT NOT NULL,
                platform TEXT NOT NULL,page_id TEXT,due_at TEXT NOT NULL,
                status TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE connected_facebook_pages(
                workspace_id TEXT NOT NULL,page_id TEXT NOT NULL,
                PRIMARY KEY(workspace_id,page_id))""")
            connection.execute("INSERT INTO connected_facebook_pages VALUES('alpha','101')")
            connection.execute("INSERT INTO connected_facebook_pages VALUES('beta','202')")
        self.moment = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)

    def db(self):
        return sqlite3.connect(os.environ["AUTOPOST_WEB_DB"])

    def post(self, post_id, workspace="alpha", platform="facebook_page", page="101",
             status="scheduled", due=None):
        with self.db() as connection:
            connection.execute("INSERT INTO app_posts VALUES(?,?,?,?,?,?)",
                               (post_id,workspace,platform,page,
                                due or self.moment.isoformat(),status))

    def tier(self, plan, active=True):
        with self.db() as connection:
            connection.execute("INSERT OR REPLACE INTO subscriptions VALUES(?,?,?,?,?,?,?,?,?)",
                ("alpha","stripe","cus_fixture","sub_fixture","active" if active else "canceled",
                 plan,4102444800,1,1))

    def test_basic_defaults_and_owner_exemption_without_owner_database(self):
        self.assertEqual(plan_limits.limits("alpha")["daily_limit_per_platform"],1)
        self.assertEqual(plan_limits.limits("alpha")["quota_timezone"],"UTC")
        self.assertFalse(plan_limits.limits("owner")["basic"])
        self.assertTrue(plan_limits.validate_destinations("owner",[]))
        self.assertTrue(plan_limits.validate_pages("owner",["1","2","3","4","5"]))
        self.assertTrue(plan_limits.reserve_delivery("owner",1,"facebook_page","101")["exempt"])
        self.tier("made-up-unlimited")
        self.assertTrue(plan_limits.limits("alpha")["basic"])
        self.tier("business",active=False)
        self.assertTrue(plan_limits.limits("alpha")["basic"])
        self.tier("business")
        self.assertFalse(plan_limits.limits("alpha")["basic"])
        self.assertTrue(plan_limits.validate_cadence("alpha",["101","303"],60))

    def test_one_page_per_platform_but_different_platforms_can_publish_together(self):
        targets=[("facebook_page",None,"101"),("instagram",None,None),("x",None,None)]
        self.assertTrue(plan_limits.validate_destinations("alpha",targets))
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"one Page"):
            plan_limits.validate_destinations("alpha",[("facebook_page",None,"101"),("facebook_page",None,"303")])
        with self.assertRaises(plan_limits.PlanLimitError):
            plan_limits.validate_destinations("alpha",[("facebook_page",None,"101"),("facebook_page",None,"101")])
        with self.assertRaises(plan_limits.PlanLimitError):
            plan_limits.validate_destinations("alpha",[("twitter",None,None),("x",None,None)])
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"one posting Page"):
            plan_limits.validate_pages("alpha",["303"])
        self.assertTrue(plan_limits.validate_pages("alpha",["101"]))
        with self.db() as connection:
            connection.execute("DELETE FROM connected_facebook_pages WHERE workspace_id='alpha'")
        self.assertTrue(plan_limits.validate_pages("alpha",["303"]))

    def test_basic_cadence_and_drafts(self):
        self.assertTrue(plan_limits.validate_cadence("alpha",["101"],86400))
        self.assertTrue(plan_limits.validate_cadence("alpha",["101"],604800))
        for cadence in (0,900,43200,True,float('nan')):
            with self.subTest(cadence=cadence),self.assertRaises(plan_limits.PlanLimitError):
                plan_limits.validate_cadence("alpha",["101"],cadence)
        self.assertTrue(plan_limits.check_schedule("alpha",[("draft",None,None)],"not-a-publishing-time"))
        self.assertTrue(plan_limits.reserve_delivery("alpha",999,"draft")["exempt"])

    def test_schedule_conflicts_utc_offsets_and_workspace_isolation(self):
        self.post(1,due="2026-11-14T01:00:00+02:00") # November 13 UTC
        self.post(2,workspace="beta",page="202")
        target=[("facebook_page",None,"101")]
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"already has a post"):
            plan_limits.check_schedule("alpha",target,"2026-11-13T20:00:00Z")
        self.assertTrue(plan_limits.check_schedule("alpha",target,"2026-11-14T01:00:00Z"))
        self.assertTrue(plan_limits.check_schedule("alpha",target,self.moment,[1]))
        self.assertTrue(plan_limits.check_schedule("alpha",[("instagram",None,None)],self.moment))
        with self.db() as connection:
            connection.execute("UPDATE app_posts SET status='cancelled' WHERE id=1")
        self.assertTrue(plan_limits.check_schedule("alpha",target,self.moment))
        for invalid in ("2026-11-13T10:00:00", "not a date", float('inf')):
            with self.subTest(invalid=invalid),self.assertRaises(plan_limits.PlanLimitError):
                plan_limits.check_schedule("alpha",target,invalid)

    def test_atomic_reservations_are_platform_scoped_and_survive_uncertain_delivery(self):
        self.post(1,status="sending")
        self.post(2,status="sending")
        self.post(3,platform="instagram",page=None,status="sending")
        self.post(4,workspace="beta",page="202",status="sending")
        first=plan_limits.reserve_delivery("alpha",1,"facebook_page","101",self.moment)
        self.assertFalse(first["already_reserved"])
        self.assertTrue(plan_limits.reserve_delivery("alpha",1,"facebook","101",self.moment)["already_reserved"])
        with self.db() as connection:
            connection.execute("UPDATE app_posts SET status='needs_review' WHERE id=1")
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"allowance is already used"):
            plan_limits.reserve_delivery("alpha",2,"facebook_page","101",self.moment)
        self.assertTrue(plan_limits.reserve_delivery("alpha",3,"instagram",None,self.moment)["reserved"])
        self.assertTrue(plan_limits.reserve_delivery("beta",4,"facebook_page","202",self.moment)["reserved"])
        self.assertTrue(plan_limits.reserve_delivery("alpha",2,"facebook_page","101",self.moment+timedelta(days=1))["reserved"])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM plan_delivery_reservations").fetchone()[0],4)

    def test_wrong_workspace_page_and_downgraded_multiple_page_connections_rejected(self):
        self.post(1,status="sending")
        for workspace,page in (("beta","101"),("alpha","999"),("alpha",None)):
            with self.subTest(workspace=workspace,page=page),self.assertRaises(plan_limits.PlanLimitError):
                plan_limits.reserve_delivery(workspace,1,"facebook_page",page,self.moment)
        with self.db() as connection:
            connection.execute("INSERT INTO connected_facebook_pages VALUES('alpha','303')")
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"one connected"):
            plan_limits.reserve_delivery("alpha",1,"facebook_page","101",self.moment)

    def test_reservation_remains_a_schedule_conflict_even_when_attempt_status_changes(self):
        self.post(1,status="sending")
        plan_limits.reserve_delivery("alpha",1,"facebook_page","101",self.moment)
        with self.db() as connection:
            connection.execute("UPDATE app_posts SET status='failed' WHERE id=1")
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"allowance"):
            plan_limits.check_schedule("alpha",[("facebook_page",None,"101")],self.moment)
        self.assertTrue(plan_limits.check_schedule("alpha",[("facebook_page",None,"101")],self.moment,[1]))

    def test_concurrent_different_posts_cannot_take_the_same_daily_allowance(self):
        self.post(1,status="sending")
        self.post(2,status="sending")
        barrier=threading.Barrier(2)
        def attempt(post_id):
            barrier.wait(timeout=5)
            try:
                plan_limits.reserve_delivery("alpha",post_id,"facebook_page","101",self.moment)
                return True
            except plan_limits.PlanLimitError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt,[1,2])),[False,True])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM plan_delivery_reservations").fetchone()[0],1)

    def test_missing_customer_storage_fails_closed_but_owner_stays_compatible(self):
        with self.db() as connection:
            connection.execute("DROP TABLE app_posts")
            connection.execute("CREATE TABLE app_posts(id INTEGER PRIMARY KEY,platform TEXT,due_at TEXT,status TEXT)")
        with self.assertRaisesRegex(plan_limits.PlanLimitError,"storage is not ready"):
            plan_limits.check_schedule("alpha",[("instagram",None,None)],self.moment)
        self.assertTrue(plan_limits.check_schedule("owner",[("facebook",None,None)],self.moment))


if __name__ == '__main__':
    unittest.main()
