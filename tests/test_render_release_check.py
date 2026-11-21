import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("render_release_check", Path(__file__).resolve().parents[1] / "scripts/qa/render_release_check.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)
ORIGIN = "https://auto-poster-ai-free-preview.onrender.com"
SHA = "a" * 40


def good(url):
    if url.endswith("/clips/jobs"):
        return {"http_status": 401, "body": ""}
    if url.endswith("/version"):
        return {"http_status": 200, "body": json.dumps({"commit": SHA})}
    if url.endswith("/manifest.webmanifest"):
        return {"http_status": 200, "body": json.dumps({"name": "Auto Poster AI", "scope": "/", "icons": [{"src": "/icon.svg"}]})}
    return {"http_status": 200, "body": "<!doctype html><html><title>Auto Poster AI</title></html>"}


class RenderReleaseTests(unittest.TestCase):
    def test_public_evidence_does_not_certify_private_workflows(self):
        getter = Mock(side_effect=good)
        report = checks.check(ORIGIN, SHA, getter=getter)
        self.assertTrue(report["public_checks_passed"])
        self.assertTrue(report["deployed_revision_verified"])
        self.assertFalse(report["ready_for_public_launch"])
        self.assertTrue(all(value == "not_checked" for value in report["private_workflow_verification"].values()))
        self.assertEqual([call.args[0] for call in getter.call_args_list], [ORIGIN + path for path in checks.PATHS])

    def test_unknown_or_wrong_revision_fails(self):
        for value in (None, "HEAD", "b" * 40):
            def getter(url):
                return {"http_status": 200, "body": json.dumps({"commit": value})} if url.endswith("/version") else good(url)
            report = checks.check(ORIGIN, SHA, getter=getter)
            self.assertFalse(report["deployed_revision_verified"])
            self.assertFalse(report["public_checks_passed"])

    def test_invalid_origins_or_expected_commit_make_no_requests(self):
        getter = Mock()
        for origin in ("http://public.com", "https://localhost", "https://127.0.0.1", "https://10.0.0.1", "https://[::1]", "https://user:secret@public.com", ORIGIN + "/path", ORIGIN + "?secret=value", "https://example.com", ORIGIN + ":444"):
            with self.assertRaises(ValueError, msg=origin):
                checks.check(origin, SHA, getter=getter)
        with self.assertRaises(ValueError): checks.check(ORIGIN, "short", getter=getter)
        getter.assert_not_called()

    def test_private_endpoint_exposure_is_a_failure(self):
        report = checks.check(ORIGIN, SHA, getter=lambda url: {"http_status": 200, "body": "sensitive-user-data"} if url.endswith("/clips/jobs") else good(url))
        self.assertFalse(report["public_checks_passed"])
        self.assertNotIn("sensitive-user-data", json.dumps(report))

    def test_network_errors_remain_unknown_and_sanitized(self):
        report = checks.check(ORIGIN, SHA, getter=lambda _: {"error": "proxy_route_unavailable"})
        self.assertFalse(report["service_reachable"])
        self.assertIsNone(report["deployed_commit"])
        self.assertFalse(report["public_checks_passed"])

    def test_transport_keeps_proxy_tls_and_disables_redirects(self):
        import requests
        with patch.object(requests, "get", side_effect=requests.exceptions.ProxyError("secret-cookie")) as request:
            result = checks._get(ORIGIN)
        self.assertEqual(result, {"error": "proxy_route_unavailable"})
        self.assertNotIn("secret-cookie", str(result))
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertEqual(request.call_args.kwargs["timeout"], (5, 15))
        self.assertNotIn("verify", request.call_args.kwargs)
        self.assertNotIn("proxies", request.call_args.kwargs)


if __name__ == "__main__": unittest.main()
