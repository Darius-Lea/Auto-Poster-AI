"""Workspace metrics and AI reports use synthetic fixtures, never live APIs."""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import analytics_workbench as analytics


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = self.folder.name + "/analytics.db"
        self.env = patch.dict(os.environ, {"AUTOPOST_WEB_DB": self.path, "AUTOPOST_PREVIEW_MODE": "false", "META_GRAPH_VERSION": "v26.0"})
        self.env.start();self.addCleanup(self.env.stop)
        with sqlite3.connect(self.path) as c:
            c.execute("""CREATE TABLE app_posts(id INTEGER PRIMARY KEY,workspace_id TEXT NOT NULL DEFAULT 'owner',content TEXT NOT NULL,platform TEXT NOT NULL,
            status TEXT NOT NULL,page_id TEXT,remote_id TEXT,due_at TEXT,created_at TEXT)""")
        analytics.init()

    def add(self, number, ws="customer-a", status="published", page="123", remote=None, platform="facebook_page", content="Test content"):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO app_posts VALUES(?,?,?,?,?,?,?,?,?)", (number,ws,content,platform,status,page,
                remote or "meta:"+page+"_"+str(number),"2026-11-13T12:00:00Z","2026-11-12T12:00:00Z"))

    def payload(self, number, reactions=3, comments=2, shares=1):
        result={"id":"123_"+str(number),"reactions":{"summary":{"total_count":reactions}},
                "comments":{"summary":{"total_count":comments}}}
        if shares is not None:result["shares"]={"count":shares}
        return Mock(status_code=200,json=Mock(return_value=result))

    def observed(self, number, ws="customer-a", reactions=3, comments=2, shares=1):
        self.add(number,ws=ws)
        with analytics._db() as c:
            post=c.execute("SELECT * FROM app_posts WHERE id=?",(number,)).fetchone()
        analytics._record(ws,dict(post),metrics={"reactions":reactions,"comments":comments,"shares":shares})

    @patch.object(analytics.requests,"get")
    def test_report_counts_are_real_workspace_scoped_and_unknown_is_not_zero(self,get):
        self.add(1,status="scheduled");self.add(2,status="setup_only");self.add(3,ws="customer-b")
        result=analytics.report("customer-a")
        self.assertEqual(result["counts"],{"scheduled":1,"setup_only":1})
        self.assertEqual(len(result["posts"]),2)
        self.assertEqual(result["posts"][0]["metrics"],{"reactions":None,"comments":None,"shares":None})
        self.assertIsNone(result["ranking"])
        self.assertIsNone(result["last_refreshed_at"])
        get.assert_not_called()

    @patch.object(analytics.meta_accounts,"get_page_token",return_value="synthetic-page-token")
    @patch.object(analytics.requests,"get")
    def test_refresh_checks_owned_page_identity_and_sends_token_only_in_header(self,get,token):
        self.add(1);self.add(2,page="456",remote="meta:123_2");self.add(3,platform="draft");self.add(4,ws="customer-b")
        get.return_value=self.payload(1,reactions=0,comments=0,shares=0)
        result=analytics.refresh_metrics("customer-a")
        self.assertEqual(result["refreshed"],1)
        self.assertEqual(result["unavailable"],2)
        token.assert_called_once_with("customer-a","123")
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0],"https://graph.facebook.com/v26.0/123_1")
        self.assertEqual(get.call_args.kwargs["headers"],{"Authorization":"Bearer synthetic-page-token"})
        self.assertEqual(get.call_args.kwargs["allow_redirects"],False)
        self.assertNotIn("synthetic-page-token",str(get.call_args.kwargs["params"]))
        post=next(p for p in result["report"]["posts"] if p["id"]==1)
        self.assertEqual(post["interactions"],0)
        self.assertNotIn("synthetic-page-token",json.dumps(result))
        with self.assertRaisesRegex(ValueError,"wait a minute"):
            analytics.refresh_metrics("customer-a")

    @patch.object(analytics.meta_accounts,"get_page_token",return_value="synthetic-page-token")
    @patch.object(analytics.requests,"get")
    def test_missing_share_count_remains_unknown_and_cannot_rank(self,get,token):
        self.add(1);get.return_value=self.payload(1,shares=None)
        result=analytics.refresh_metrics("customer-a")["report"]
        self.assertEqual(result["measured_posts"],1)
        self.assertEqual(result["comparable_posts"],0)
        self.assertIsNone(result["posts"][0]["metrics"]["shares"])
        self.assertIsNone(result["posts"][0]["interactions"])
        self.assertIsNone(result["ranking"])

    @patch.object(analytics.meta_accounts,"get_page_token",side_effect=PermissionError("synthetic-private-token"))
    @patch.object(analytics.requests,"get")
    def test_missing_connection_never_calls_api_or_exposes_private_error(self,get,token):
        self.add(1)
        result=analytics.refresh_metrics("customer-a")
        self.assertEqual(result["refreshed"],0)
        self.assertNotIn("synthetic-private-token",json.dumps(result))
        self.assertIsNone(result["report"]["posts"][0]["metrics"]["reactions"])
        get.assert_not_called()

    @patch.object(analytics.meta_accounts,"get_page_token",return_value="test-token")
    @patch.object(analytics.requests,"get")
    def test_mismatched_api_identity_is_not_saved(self,get,token):
        self.add(1);get.return_value=self.payload(9)
        result=analytics.refresh_metrics("customer-a")
        self.assertEqual(result["refreshed"],0)
        self.assertEqual(result["report"]["measured_posts"],0)

    def test_rankings_require_three_complete_measured_posts(self):
        self.observed(1,reactions=1);self.observed(2,reactions=10)
        self.assertIsNone(analytics.report("customer-a")["ranking"])
        self.observed(3,reactions=3)
        result=analytics.report("customer-a")
        self.assertEqual(result["ranking"]["best"][0]["id"],2)
        self.assertEqual(result["ranking"]["worst"][0]["id"],1)
        self.assertIn("not causal",result["ranking"]["basis"])

    @patch.object(analytics.app_control,"allowed",return_value=True)
    @patch.object(analytics.ai_providers,"generate_text")
    def test_paid_analysis_uses_only_workspace_evidence_and_guidance_requires_optin(self,generate,allowed):
        for n in range(1,4):self.observed(n)
        self.add(9,ws="customer-b",content="PRIVATE OTHER CUSTOMER")
        generate.return_value={"text":"Try shorter introductions; available metrics do not show reach.","provider":"openai","model":"fixture"}
        result=analytics.analyze("customer-a")
        allowed.assert_called_with(workspace_id="customer-a")
        self.assertEqual(result["provider"],"openai")
        self.assertNotIn("PRIVATE OTHER CUSTOMER",generate.call_args.args[0])
        self.assertIn("untrusted",generate.call_args.kwargs["system"])
        self.assertEqual(analytics.optimization_guidance("customer-a"),"")
        self.assertIsNotNone(analytics.report("customer-a")["last_analysis"])
        with self.assertRaisesRegex(ValueError,"five minutes"):
            analytics.analyze("customer-a")

    @patch.object(analytics.app_control,"allowed",return_value=False)
    @patch.object(analytics.ai_providers,"generate_text")
    def test_unpaid_analysis_and_insufficient_data_never_call_ai(self,generate,allowed):
        with self.assertRaises(PermissionError):analytics.analyze("customer-a")
        allowed.return_value=True
        with self.assertRaisesRegex(ValueError,"three posts"):
            analytics.analyze("customer-a")
        generate.assert_not_called()

    @patch.object(analytics.app_control,"allowed",return_value=True)
    @patch.object(analytics.ai_providers,"generate_text")
    def test_opt_in_only_updates_text_guidance_and_disable_clears_it(self,generate,allowed):
        for n in range(1,4):self.observed(n)
        analytics.set_optimization("customer-a",True)
        generate.return_value={"text":"Use clear local context.","provider":"gemini","model":"fixture"}
        before=analytics.report("customer-a")["counts"]
        analytics.analyze("customer-a")
        self.assertEqual(analytics.optimization_guidance("customer-a"),"Use clear local context.")
        self.assertEqual(analytics.report("customer-a")["counts"],before)
        analytics.set_optimization("customer-a",False)
        self.assertEqual(analytics.optimization_guidance("customer-a"),"")
        with self.assertRaises(ValueError):analytics.set_optimization("customer-a","yes")

    @patch.object(analytics.app_control,"allowed",return_value=True)
    @patch.object(analytics,"_refresh")
    @patch.object(analytics,"analyze")
    def test_background_tick_requires_optin_and_runs_workspace_only_once_daily(self,analyze,refresh,allowed):
        self.assertEqual(analytics.tick()["processed"],0)
        analytics.set_optimization("customer-a",True)
        refresh.return_value={"refreshed":3,"report":{"comparable_posts":3}}
        first=analytics.tick();second=analytics.tick()
        self.assertEqual(first["processed"],1)
        self.assertEqual(second["processed"],0)
        refresh.assert_called_once_with("customer-a",limit=3)
        analyze.assert_called_once_with("customer-a")

    @patch.object(analytics.requests,"get")
    @patch.object(analytics.ai_providers,"generate_text")
    def test_preview_never_fetches_metrics_or_generates_analysis(self,generate,get):
        with patch.dict(os.environ,{"AUTOPOST_PREVIEW_MODE":"true"}):
            with self.assertRaises(PermissionError):analytics.refresh_metrics("customer-a")
            with self.assertRaises(PermissionError):analytics.analyze("customer-a")
            self.assertEqual(analytics.tick()["processed"],0)
        get.assert_not_called();generate.assert_not_called()

    @patch.object(analytics.app_control,"allowed",return_value=True)
    @patch.object(analytics.ai_providers,"generate_text")
    def test_daily_ai_budget_prevents_another_paid_attempt(self,generate,allowed):
        for n in range(1,4):self.observed(n)
        import time
        with analytics._db() as c:
            c.execute("INSERT INTO analytics_ai_quota VALUES(?,?,10)",("customer-a",int(time.time())//86400))
        with self.assertRaisesRegex(ValueError,"Daily AI report limit"):
            analytics.analyze("customer-a")
        generate.assert_not_called()

    @patch.object(analytics.meta_accounts,"get_page_token",return_value="test-token")
    @patch.object(analytics.requests,"get")
    def test_failed_refresh_keeps_last_known_counts_with_observation_time(self,get,token):
        self.observed(1,reactions=7,comments=2,shares=1)
        import requests
        get.side_effect=requests.ConnectionError("synthetic-secret-do-not-expose")
        result=analytics.refresh_metrics("customer-a")
        post=result["report"]["posts"][0]
        self.assertEqual(post["interactions"],10)
        self.assertIsNotNone(post["observed_at"])
        self.assertIsNotNone(post["metrics_error"])
        self.assertNotIn("synthetic-secret",json.dumps(result))

    @patch.object(analytics.app_control,"allowed",return_value=True)
    @patch.object(analytics.ai_providers,"generate_text")
    def test_disabling_optimization_during_analysis_does_not_restore_guidance(self,generate,allowed):
        for n in range(1,4):self.observed(n)
        analytics.set_optimization("customer-a",True)
        def finish(*args,**kwargs):
            analytics.set_optimization("customer-a",False)
            return {"text":"Later advice","provider":"openai","model":"fixture"}
        generate.side_effect=finish
        analytics.analyze("customer-a")
        self.assertEqual(analytics.optimization_guidance("customer-a"),"")

    def test_owner_schema_before_migration_does_not_leak_to_customer(self):
        with sqlite3.connect(self.path) as c:
            c.execute('ALTER TABLE app_posts RENAME TO migrated_posts')
            c.execute('CREATE TABLE app_posts(id INTEGER PRIMARY KEY,content TEXT,platform TEXT,status TEXT,page_id TEXT,remote_id TEXT,due_at TEXT,created_at TEXT)')
            c.execute("INSERT INTO app_posts VALUES(1,'Owner private content','facebook','published','123','meta:123_1','now','then')")
        self.assertEqual(len(analytics.report("owner")["posts"]),1)
        self.assertEqual(analytics.report("customer-a")["posts"],[])


if __name__=="__main__":unittest.main()
