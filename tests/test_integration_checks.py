"""Read-only diagnostics: synthetic tokens only, no live API calls."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

SPEC = importlib.util.spec_from_file_location("integration_checks", Path(__file__).resolve().parents[1] / "scripts/qa/check_integrations.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class IntegrationChecksTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = str(Path(self.folder.name) / "accounts.db")
        self.env = {"META_APP_ID": "synthetic-app", "META_APP_SECRET": "synthetic-meta-secret",
                    "META_OAUTH_REDIRECT_URI": "https://app.example.test/facebook/callback",
                    "META_PAGES_ENCRYPTION_SECRET": "synthetic-secret-more-than-32-characters",
                    "CANVA_CLIENT_ID": "synthetic-client", "CANVA_CLIENT_SECRET": "synthetic-canva-secret",
                    "CANVA_REDIRECT_URI": "https://app.example.test/connections/canva/callback",
                    "OPENAI_API_KEY": "synthetic-openai-secret", "ANTHROPIC_API_KEY": "synthetic-anthropic-secret",
                    "GEMINI_API_KEY": "synthetic-gemini-secret", "AUTOPOST_WEB_DB": self.database}

    def by_name(self, report):
        return {item["service"]: item for item in report["services"]}

    def seal(self, payload):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(self.env["META_PAGES_ENCRYPTION_SECRET"].encode()).digest())).encrypt(payload.encode())

    def database_fixture(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute("CREATE TABLE connected_facebook_pages(workspace_id,page_id,encrypted_access_token)")
            conn.execute("CREATE TABLE editor_connections(workspace_id,provider,credentials)")
            conn.execute("INSERT INTO connected_facebook_pages VALUES(?,?,?)", ("mine", "123", self.seal("synthetic-page-token")))
            conn.execute("INSERT INTO connected_facebook_pages VALUES(?,?,?)", ("someone-else", "456", self.seal("other-customer-token")))
            conn.execute("INSERT INTO editor_connections VALUES(?,?,?)", ("mine", "canva", self.seal(json.dumps({"access_token": "synthetic-canva-token", "expires_at": time.time() + 3600}))))

    def test_configuration_does_not_probe_or_create_database(self):
        getter = Mock()
        report = checks.check(self.env, workspace="mine", getter=getter)
        getter.assert_not_called()
        self.assertFalse(Path(self.database).exists())
        services = self.by_name(report)
        self.assertTrue(services["canva"]["configured"])
        self.assertEqual(services["canva"]["account_verification"], "not_checked")
        self.assertEqual(services["facebook"]["storage_inspection"], "database_not_found")
        self.assertEqual(services["muse.ai"]["account_verification"], "unsupported")
        for value in self.env.values():
            if "synthetic" in value:
                self.assertNotIn(value, json.dumps(report))

    def test_callback_validation_catches_wrong_docs_path_and_host_injection(self):
        for uri in ("https://app.example.test/creative/canva/callback", "https://user:password@app.example.test/connections/canva/callback", "https://app.example.test/connections/canva/callback?secret=hidden"):
            env = {**self.env, "CANVA_REDIRECT_URI": uri}
            canva = self.by_name(checks.check(env))["canva"]
            self.assertFalse(canva["configured"])
            self.assertNotIn(uri, str(canva))

    def test_model_catalog_verification_does_not_claim_text_generation(self):
        def getter(url, headers):
            self.assertTrue(url.startswith("https://"))
            return {"http_status": 200, "payload": {"data": [], "models": []}}
        services = self.by_name(checks.check(self.env, probe=True, getter=getter))
        for name in checks.AI:
            self.assertEqual(services[name]["account_verification"], "read_only_api_verified")
            self.assertEqual(services[name]["workflow_verification"], "not_checked")
            self.assertIn("generation", services[name]["probe_scope"])

    def test_customer_probes_are_scoped_and_database_unchanged(self):
        self.database_fixture()
        before = Path(self.database).read_bytes()
        calls = []
        def getter(url, headers):
            calls.append((url, headers))
            if "graph.facebook" in url:
                return {"http_status": 200, "payload": {"id": "123"}}
            if "api.canva" in url:
                return {"http_status": 200, "payload": {"items": []}}
            return {"http_status": 401}
        report = checks.check(self.env, probe=True, workspace="mine", getter=getter)
        services = self.by_name(report)
        self.assertEqual(services["facebook"]["accounts_checked"], 1)
        self.assertEqual(services["facebook"]["accounts_verified"], 1)
        self.assertEqual(services["canva"]["account_verification"], "read_only_api_verified")
        self.assertEqual(before, Path(self.database).read_bytes())
        self.assertNotIn("456", str(calls))
        self.assertNotIn("synthetic-page-token", json.dumps(report))
        self.assertNotIn("synthetic-canva-token", json.dumps(report))

    def test_expired_canva_token_is_not_refreshed(self):
        self.database_fixture()
        with sqlite3.connect(self.database) as conn:
            conn.execute("UPDATE editor_connections SET credentials=?", (self.seal(json.dumps({"access_token": "expired-secret", "expires_at": 0})),))
        getter = Mock(return_value={"http_status": 403})
        services = self.by_name(checks.check(self.env, probe=True, workspace="mine", getter=getter))
        self.assertEqual(services["canva"]["account_verification"], "token_expired_reconnect_in_app")
        self.assertFalse(any("canva.com" in call.args[0] for call in getter.call_args_list))

    def test_network_denial_is_not_success(self):
        services = self.by_name(checks.check(self.env, probe=True, getter=lambda *_: {"http_status": 403}))
        self.assertEqual(services["openai"]["account_verification"], "authorization_or_network_policy_denied")
        self.assertEqual(services["openai"]["workflow_verification"], "not_checked")

    def test_http_transport_blocks_redirect_and_sanitizes_error(self):
        import requests
        with patch.object(requests, "get", side_effect=requests.ConnectionError("secret-token-in-url")) as request:
            result = checks._get("https://api.openai.com/v1/models", {"Authorization": "Bearer synthetic-secret"})
        self.assertEqual(result, {"error": "network_or_tls_unavailable"})
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertEqual(request.call_args.kwargs["timeout"], (5, 15))

    def test_unconfigured_services_make_no_requests_even_with_probe(self):
        getter = Mock()
        report = checks.check({}, probe=True, getter=getter)
        getter.assert_not_called()
        self.assertFalse(any(item["configured"] for item in report["services"] if item["service"] in checks.AI))

    def test_clip_runtime_presence_does_not_enable_hosted_service(self):
        with patch.object(checks.importlib.util, "find_spec", return_value=object()), patch.object(checks.shutil, "which", return_value="/synthetic/binary"), patch.object(checks.Path, "is_file", return_value=True):
            disabled = checks._clip_runtime({})
            enabled = checks._clip_runtime({"AUTOPOST_CLIP_ENABLED": "true"})
        self.assertTrue(disabled["runtime_dependencies_present"])
        self.assertFalse(disabled["configured"])
        self.assertTrue(enabled["configured"])
        self.assertEqual(enabled["workflow_verification"], "not_checked")
        self.assertEqual(enabled["render_verification"], "not_checked")

    def test_workspace_is_required_before_probing_customer_tokens(self):
        self.database_fixture()
        getter = Mock(return_value={"http_status": 401})
        report = checks.check(self.env, probe=True, getter=getter)
        self.assertFalse(report["workspace_selected"])
        self.assertFalse(any("canva.com" in call.args[0] or "facebook.com" in call.args[0] for call in getter.call_args_list))

    def test_private_response_body_is_never_reflected_in_report(self):
        private = {"data": [], "models": [], "email": "private-customer@example.test", "token": "private-response-secret"}
        report = checks.check(self.env, probe=True, getter=lambda *_: {"http_status": 200, "payload": private})
        self.assertNotIn(private["email"], json.dumps(report))
        self.assertNotIn(private["token"], json.dumps(report))


if __name__ == "__main__":
    unittest.main()
