"""No live API calls: fallback and privacy checks use only synthetic fixtures."""
import os
import unittest
from unittest.mock import Mock, patch

import requests
import ai_providers as ai


def response(status=200, data=None, retry=None):
    r = Mock(status_code=status, headers={} if retry is None else {"Retry-After": retry})
    r.json.return_value = data if data is not None else {"choices": [{"message": {"content": "A real generated draft"}, "finish_reason": "stop"}]}
    return r


def anthropic(text="Anthropic draft"):
    return response(data={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "OPENAI_API_KEY": "synthetic-openai-secret", "ANTHROPIC_API_KEY": "synthetic-anthropic-secret",
            "GEMINI_API_KEY": "synthetic-gemini-secret", "AUTOPOST_AI_PROVIDERS": "openai,anthropic,gemini",
            "AUTOPOST_AI_PREFERRED": "openai", "OPENAI_MODEL": "gpt-4.1-mini",
            "ANTHROPIC_MODEL": "claude-sonnet-4-20250514", "GEMINI_MODEL": "gemini-2.5-flash",
        }, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        ai._COOLDOWNS.clear()
        self.addCleanup(ai._COOLDOWNS.clear)

    @patch.object(ai.requests, "post")
    def test_openai_returns_draft_metadata_with_safe_timeout(self, post):
        post.return_value = response()
        result = ai.generate_text("Write a helpful post", system="Only verified facts")
        self.assertEqual(result["provider"], "openai")
        self.assertFalse(result["fallback"])
        self.assertEqual(result["text"], "A real generated draft")
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 45))
        self.assertEqual(post.call_args.kwargs["json"]["messages"][0]["content"], "Only verified facts")
        self.assertNotIn("synthetic-openai-secret", str(result))

    @patch.object(ai.requests, "post")
    def test_quota_falls_back_then_retries_preferred_after_retry_after(self, post):
        with patch.object(ai.time, "time", return_value=1000):
            post.side_effect = [response(429, retry="120"), anthropic()]
            first = ai.generate_text("Create a post")
            self.assertEqual(first["provider"], "anthropic")
            self.assertTrue(first["fallback"])
            self.assertEqual(first["attempts"][0]["reason"], "usage_limit")
        with patch.object(ai.time, "time", return_value=1060):
            post.side_effect = [anthropic("Second draft")]
            second = ai.generate_text("Create another post")
            self.assertEqual(second["attempts"][0]["status"], "skipped")
            self.assertEqual(second["provider"], "anthropic")
            self.assertEqual(ai.provider_status()["providers"][0]["retry_after_seconds"], 60)
        with patch.object(ai.time, "time", return_value=1121):
            post.side_effect = [response()]
            third = ai.generate_text("Try best again")
            self.assertEqual(third["provider"], "openai")
            self.assertFalse(third["fallback"])

    @patch.object(ai.requests, "post")
    def test_transient_failures_try_gemini_with_header_auth_not_query(self, post):
        gemini = response(data={"candidates": [{"content": {"parts": [{"text": "Gemini draft"}]}, "finishReason": "STOP"}]})
        post.side_effect = [requests.Timeout("synthetic-secret"), response(503), gemini]
        result = ai.generate_text("Create a local post", system="No invented claims")
        self.assertEqual(result["provider"], "gemini")
        self.assertNotIn("synthetic-secret", str(result))
        url = post.call_args.args[0]
        self.assertNotIn("?key=", url)
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "synthetic-gemini-secret")
        self.assertEqual(post.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"], "No invented claims")

    @patch.object(ai.requests, "post")
    def test_rejected_request_is_not_routed_around(self, post):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                post.reset_mock(); post.return_value = response(status)
                with self.assertRaises(ai.AIRequestError) as caught:
                    ai.generate_text("Generate some content")
                self.assertEqual(post.call_count, 1)
                self.assertNotIn("synthetic-", str(caught.exception))

    @patch.object(ai.requests, "post")
    def test_refusals_from_all_three_providers_stop_fallback(self, post):
        cases = [
            ("openai", {"choices": [{"message": {"refusal": "No"}, "finish_reason": "stop"}]}),
            ("anthropic", {"content": [], "stop_reason": "refusal"}),
            ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
        ]
        for preferred, payload in cases:
            with self.subTest(preferred=preferred):
                post.reset_mock(); post.return_value = response(data=payload)
                with self.assertRaises(ai.AIRequestError):
                    ai.generate_text("Requested text", preferred=preferred)
                self.assertEqual(post.call_count, 1)

    @patch.object(ai.requests, "post")
    def test_empty_output_fails_over_without_fabricating_a_draft(self, post):
        post.side_effect = [response(data={"choices": []}), anthropic("Verified fallback draft")]
        result = ai.generate_text("Make a draft")
        self.assertEqual(result["text"], "Verified fallback draft")
        self.assertEqual(result["attempts"][0]["reason"], "invalid_response")

    @patch.object(ai.requests, "post")
    def test_all_unavailable_preserves_safe_attempts_and_retry(self, post):
        post.side_effect = [response(429, retry="45"), response(503, retry="90"), response(429, retry="60")]
        with self.assertRaises(ai.AIUnavailable) as caught:
            ai.generate_text("Generate a post")
        self.assertEqual(caught.exception.retry_after, 45)
        self.assertEqual(len(caught.exception.attempts), 3)
        self.assertNotIn("synthetic-", str(caught.exception.attempts))

    @patch.object(ai.requests, "post")
    def test_unconfigured_services_never_make_requests(self, post):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}):
            with self.assertRaises(ai.AIUnavailable):
                ai.generate_text("Write a post")
            self.assertTrue(all(not p["configured"] for p in ai.provider_status()["providers"]))
        post.assert_not_called()

    @patch.object(ai.requests, "post")
    def test_configured_order_can_disable_and_prefer_services(self, post):
        with patch.dict(os.environ, {"AUTOPOST_AI_PROVIDERS": "gemini,openai", "AUTOPOST_AI_PREFERRED": "gemini"}):
            post.return_value = response(data={"candidates": [{"content": {"parts": [{"text": "Gemini first"}]}}]})
            result = ai.generate_text("Write this post")
            self.assertEqual(result["provider"], "gemini")
            self.assertFalse(ai.provider_status()["providers"][1]["configured"])
        with self.assertRaises(ValueError):
            ai.generate_text("Write", preferred="https://attacker.invalid")

    @patch.object(ai.requests, "post")
    def test_invalid_input_or_model_cannot_inject_api_endpoint(self, post):
        for prompt in (None, "", "x", "x" * 20001):
            with self.assertRaises(ValueError):
                ai.generate_text(prompt)
        with self.assertRaises(ValueError):
            ai.generate_text("Hello", max_tokens=True)
        with patch.dict(os.environ, {"OPENAI_MODEL": "../../attacker", "AUTOPOST_AI_PROVIDERS": "openai"}):
            with self.assertRaises(ai.AIUnavailable):
                ai.generate_text("Hello there")
        post.assert_not_called()

    def test_retry_after_date_and_invalid_values_are_bounded(self):
        self.assertGreaterEqual(ai._retry_after(response(429, retry="-1")), 1)
        self.assertEqual(ai._retry_after(response(429, retry="NaN")), 60)
        self.assertEqual(ai._retry_after(response(429, retry="999999999")), 60)
        self.assertEqual(ai._retry_after(response(429, retry="bad date")), 60)
        date = "Sun, 01 Jan 2040 12:00:00 GMT"
        self.assertGreaterEqual(ai._retry_after(response(429, retry=date)), 1)


if __name__ == "__main__":
    unittest.main()
