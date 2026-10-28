import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from core.facebook import FacebookClient, FacebookDeliveryUnknown, FacebookError


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class FacebookTests(unittest.TestCase):
    def setUp(self):
        self.client = FacebookClient("123", "secret-page-token")
        self.image = tempfile.NamedTemporaryFile()
        self.image.write(b"image fixture")
        self.image.flush()
        self.addCleanup(self.image.close)

    @patch("core.facebook.requests.post")
    def test_photo_success_uses_bearer_and_post_id(self, post):
        post.return_value = response({"id": "456", "post_id": "123_456"})
        self.assertEqual(self.client.publish_photo(self.image.name, "Caption"), "123_456")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v26.0/123/photos")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secret-page-token"})
        self.assertEqual(kwargs["data"], {"caption": "Caption", "published": "true"})
        self.assertNotIn("access_token", kwargs["data"])
        self.assertFalse(kwargs["allow_redirects"])
        post.assert_called_once()

    @patch("core.facebook.requests.post")
    def test_photo_id_fallback(self, post):
        post.return_value = response({"id": "456"})
        self.assertEqual(self.client.publish_photo(self.image.name, "Caption"), "456")

    @patch("core.facebook.requests.post")
    def test_graph_error_preserves_details_and_redacts_token(self, post):
        post.return_value = response({"error": {"message": "Invalid secret-page-token",
                                              "code": 190, "error_subcode": 463,
                                              "fbtrace_id": "trace123"}}, 400)
        with self.assertRaises(FacebookError) as caught:
            self.client.publish_photo(self.image.name, "Caption")
        self.assertNotIsInstance(caught.exception, FacebookDeliveryUnknown)
        self.assertIn("code=190", str(caught.exception))
        self.assertIn("error_subcode=463", str(caught.exception))
        self.assertIn("trace123", str(caught.exception))
        self.assertNotIn("secret-page-token", str(caught.exception))

    @patch("core.facebook.requests.post")
    def test_ambiguous_responses_never_retry(self, post):
        for payload, status in [({}, 200), ({"id": None}, 200),
                                ({"error": {"message": "internal"}}, 500)]:
            with self.subTest(payload=payload, status=status):
                post.reset_mock()
                post.return_value = response(payload, status)
                with self.assertRaises(FacebookDeliveryUnknown):
                    self.client.publish_photo(self.image.name, "Caption")
                post.assert_called_once()

    @patch("core.facebook.requests.post")
    def test_transport_error_does_not_expose_exception_token_or_retry(self, post):
        post.side_effect = requests.Timeout("https://example/?access_token=secret-page-token")
        with self.assertRaises(FacebookDeliveryUnknown) as caught:
            self.client.publish_photo(self.image.name, "Caption")
        self.assertNotIn("secret-page-token", str(caught.exception))
        post.assert_called_once()

    @patch("core.facebook.requests.post")
    def test_malformed_success_is_unknown(self, post):
        post.return_value = response(None)
        post.return_value.json.side_effect = ValueError("bad JSON secret-page-token")
        with self.assertRaises(FacebookDeliveryUnknown):
            self.client.publish_photo(self.image.name, "Caption")

    @patch("core.facebook.requests.get")
    def test_check_page_read_only_token_identity(self, get):
        get.return_value = response({"id": "123", "name": "Brand"})
        self.assertEqual(self.client.check_page()["name"], "Brand")
        self.assertEqual(get.call_args.args[0], "https://graph.facebook.com/v26.0/me")
        self.assertEqual(get.call_args.kwargs["params"], {"fields": "id,name"})
        self.assertNotIn("secret-page-token", get.call_args.args[0])

    @patch("core.facebook.requests.get")
    def test_check_page_rejects_wrong_or_missing_identity(self, get):
        for payload in [{"id": "456"}, {}]:
            get.return_value = response(payload)
            with self.assertRaises(FacebookError):
                self.client.check_page()

    def test_config_requires_page_token_and_version(self):
        for args in [("", "token"), ("not-a-page", "token"), ("123", ""), ("123", "token", "../../")]:
            with self.subTest(args=args), self.assertRaises(FacebookError):
                FacebookClient(*args)
        with patch.dict(os.environ, {"META_PAGE_ID": "123", "META_PAGE_ACCESS_TOKEN": "token",
                                    "META_GRAPH_VERSION": "v25.0"}):
            self.assertEqual(FacebookClient.from_env().version, "v25.0")


if __name__ == "__main__":
    unittest.main()
