import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from adapters.base import Record
from core import delivery, publisher
from core.facebook import FacebookDeliveryUnknown, FacebookError


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "pipeline.db")
        environment = {
            "PUBLISH_BACKEND": "meta",
            "META_PAGE_ID": "123",
            "META_PAGE_ACCESS_TOKEN": "test-page-token",
            "AUTOPOST_DB": self.db_path,
        }
        self.start(patch.dict(os.environ, environment, clear=True))
        self.start(patch.object(main, "DB_PATH", self.db_path))
        self.start(patch.object(main, "load_dotenv"))
        self.download = self.start(patch.object(main, "download_image", return_value="downloaded.jpg"))
        self.stamp = self.start(patch.object(main.watermark, "stamp", return_value="stamped.jpg"))
        self.caption = self.start(patch.object(main.caption_mod, "make_caption", return_value="Fixture caption"))
        self.publish = self.start(patch.object(main.publisher, "publish", return_value="meta:123_456"))
        self.get_adapter = self.start(patch.object(main, "get_adapter"))
        self.notify = self.start(patch.object(main.notify, "send_telegram_summary"))
        self.city = {
            "city": "Fort Wayne",
            "workspace_key": "fort-wayne",
            "logo": "assets/fort-wayne.png",
            "sources": [{"adapter": "fixture", "url": "https://source.invalid/roster"}],
        }
        self.record = Record("Synthetic Fixture", "Example", "10/28/2026",
                             "https://source.invalid/detail", "https://source.invalid/photo.jpg", 30)
        self.adapter = Mock(errors=[])
        self.adapter.fetch.return_value = [self.record]
        self.get_adapter.return_value = self.adapter
        self.conn = main.init_db()
        self.addCleanup(self.conn.close)
        self.destination = publisher.destination(self.city["workspace_key"])

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def process(self, city=None, args=None):
        args = args or SimpleNamespace(dry_run=False, dump_html=False, limit=1, attempted=0)
        results, errors = {}, {}
        main.process_city(city or self.city, self.conn, args, results, errors)
        return results, errors

    def test_legacy_seen_and_queue_delivery_do_not_block_facebook(self):
        h = main.record_hash(self.record)
        main.mark_seen(self.conn, h)
        delivery.claim(self.conn, h, "queue:fort-wayne")
        delivery.complete(self.conn, h, "queue:fort-wayne", "outbox/fixture.json")
        results, errors = self.process()
        self.publish.assert_called_once()
        self.assertEqual(len(results["Fort Wayne"]), 1)
        self.assertEqual(errors["Fort Wayne"], [])
        self.assertTrue(main.is_seen(self.conn, h))

    def test_success_is_confirmed_and_next_pass_is_deduplicated(self):
        self.process()
        self.assertEqual(self.conn.execute(
            "SELECT status, remote_ref FROM deliveries WHERE destination = ?", (self.destination,)
        ).fetchone(), ("published", "meta:123_456"))
        self.assertEqual(self.conn.execute(
            "SELECT remote_ref, original_caption, source_url FROM custody_posts WHERE destination = ?",
            (self.destination,),
        ).fetchone(), ("123_456", "Fixture caption", self.record.source_url))
        results, errors = self.process()
        self.publish.assert_called_once()
        self.assertEqual(results["Fort Wayne"], [])
        self.assertEqual(errors["Fort Wayne"], [])
        self.assertTrue(self.adapter.seen_check(
            self.record.name, self.record.booking_date, self.record.source_url))

    def test_explicit_facebook_rejection_releases_claim_for_retry(self):
        self.publish.side_effect = [FacebookError("Facebook HTTP 400; code=200"), "meta:123_456"]
        results, errors = self.process()
        self.assertEqual(results["Fort Wayne"], [])
        self.assertIn("code=200", errors["Fort Wayne"][0])
        self.assertFalse(delivery.blocked(self.conn, main.record_hash(self.record), self.destination))
        results, errors = self.process()
        self.assertEqual(self.publish.call_count, 2)
        self.assertEqual(len(results["Fort Wayne"]), 1)
        self.assertEqual(errors["Fort Wayne"], [])

    def test_ambiguous_upload_is_unresolved_and_not_retried(self):
        self.publish.side_effect = FacebookDeliveryUnknown("Check the Page before retrying")
        results, errors = self.process()
        self.assertEqual(results["Fort Wayne"], [])
        self.assertIn(self.destination, errors["Fort Wayne"][0])
        unresolved = delivery.list_unresolved(self.conn)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["status"], "uncertain")
        self.process()
        self.publish.assert_called_once()

    def test_limit_is_shared_across_sources(self):
        self.city["sources"].append({"adapter": "second", "url": "https://source.invalid/second"})
        other = Record("Second Fixture", "Example", "10/28/2026",
                       "https://source.invalid/detail2", "https://source.invalid/photo2.jpg")
        self.adapter.fetch.return_value = [self.record, other]
        args = SimpleNamespace(dry_run=False, dump_html=False, limit=1, attempted=0)
        results, errors = self.process(args=args)
        self.publish.assert_called_once()
        self.get_adapter.assert_called_once()
        self.assertEqual(args.attempted, 1)
        self.assertEqual(len(results["Fort Wayne"]), 1)
        self.assertEqual(errors["Fort Wayne"], [])

    def test_main_returns_failure_when_publish_is_rejected(self):
        self.start(patch.object(main.FacebookClient, "from_env", return_value=Mock(
            page_id="123", check_page=Mock(return_value={"id": "123", "name": "Fixture Page"}))))
        self.start(patch.object(main, "load_config", return_value=[self.city]))
        self.publish.side_effect = FacebookError("Permission rejected")
        with patch("sys.argv", ["main.py", "--limit", "1"]):
            self.assertEqual(main.main(), 1)
        self.notify.assert_called_once()
        self.assertTrue(self.notify.call_args.args[1]["Fort Wayne"])

    def test_missing_page_token_fails_preflight_without_scraping(self):
        os.environ.pop("META_PAGE_ACCESS_TOKEN")
        with patch("sys.argv", ["main.py"]):
            self.assertEqual(main.main(), 1)
        self.get_adapter.assert_not_called()
        self.publish.assert_not_called()
        self.notify.assert_not_called()

    def test_check_facebook_is_read_only_and_does_not_scrape(self):
        client = Mock()
        client.check_page.return_value = {"id": "123", "name": "Fixture Page"}
        self.start(patch.object(main.FacebookClient, "from_env", return_value=client))
        with patch("sys.argv", ["main.py", "--check-facebook"]):
            self.assertEqual(main.main(), 0)
        client.check_page.assert_called_once_with()
        client.publish_photo.assert_not_called()
        self.get_adapter.assert_not_called()
        self.publish.assert_not_called()
        self.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
