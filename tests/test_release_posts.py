"""Tests for standalone RELEASED posts when a booking leaves the roster."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import sqlite3
import unittest
from unittest.mock import Mock, patch

from core import custody, delivery, grouped, release_posts
from core.facebook import FacebookDeliveryUnknown
from core.roster_monitor import source_key


def hash_fields(name, booking_date, source_url):
    raw = f"{name}|{booking_date}|{source_url}".lower().encode("utf-8")
    return sha256(raw).hexdigest()


class ReleasePostTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        delivery.initialize(self.conn)
        custody.initialize(self.conn)
        grouped.initialize(self.conn)
        release_posts.initialize(self.conn)
        self.city = {"city": "Fort Wayne", "workspace_key": "fort-wayne",
                     "logo": "fixture-logo.png",
                     "sources": [{"adapter": "allen_sheriff", "kind": "booking",
                                  "url": "https://source.example/roster"}]}
        self.destination = "meta:123:fort-wayne"
        self.source = source_key(self.city, self.city["sources"][0])
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.person = hash_fields("Fixture Person", "01/01/2000",
                                  "https://source.example/detail/0")
        self.members = {self.person, *[f"other-{i}" for i in range(9)]}
        self.absent = self.members - {self.person}
        self.client = Mock(page_id="123")
        self.client.publish_photo.return_value = "123_999"
        self.client.publish_message.return_value = "123_1000"
        self.download = patch("core.release_posts.download_image",
                              return_value="/tmp/fixture-release.jpg")
        self.download.start()
        self.addCleanup(self.download.stop)
        self.stamp = patch("core.release_posts.watermark.stamp",
                           side_effect=lambda image, logo, out: out)
        self.stamp.start()
        self.addCleanup(self.stamp.stop)

    def track(self, **kwargs):
        custody.track(self.conn, self.person, self.destination, self.source,
                      "123_456", "Original booking caption",
                      "https://source.example/detail/0", "Fixture Person", **kwargs)

    def confirm_departure(self):
        custody.observe(self.conn, self.source, self.members, self.now)
        custody.observe(self.conn, self.source, self.absent, self.now + timedelta(hours=1))
        changed = self.now + timedelta(hours=2)
        custody.observe(self.conn, self.source, self.absent, changed)
        custody.mark_applied(self.conn, self.person, self.destination,
                             "no_longer_listed", changed)
        return changed

    def publish(self, now):
        return release_posts.publish_pending(self.conn, self.client, self.city,
                                             self.destination, now=now)

    def test_photo_release_post_published_once(self):
        self.track(image_url="https://source.example/photo/0.jpg",
                   charge="Example charge")
        changed = self.confirm_departure()
        report = self.publish(changed + timedelta(hours=1))
        self.assertEqual(report["published"], 1)
        self.assertEqual(report["errors"], [])
        self.client.publish_photo.assert_called_once()
        self.client.publish_message.assert_not_called()
        caption = self.client.publish_photo.call_args.args[1]
        self.assertIn("RELEASED — ALLEN COUNTY JAIL", caption)
        self.assertIn("Fixture Person", caption)
        self.assertIn("Charges at booking: Example charge.", caption)
        row = self.conn.execute(
            "SELECT status, post_id FROM release_posts WHERE record_hash = ?",
            (self.person,)).fetchone()
        self.assertEqual(row, ("published", "123_999"))
        again = self.publish(changed + timedelta(hours=2))
        self.assertEqual(again["published"], 0)
        self.client.publish_photo.assert_called_once()

    def test_text_post_when_no_photo_on_file(self):
        self.track()
        changed = self.confirm_departure()
        report = self.publish(changed + timedelta(hours=1))
        self.assertEqual(report["published"], 1)
        self.client.publish_message.assert_called_once()
        self.client.publish_photo.assert_not_called()
        caption = self.client.publish_message.call_args.args[0]
        self.assertIn("Fixture Person", caption)
        self.assertNotIn("Charges at booking", caption)

    def test_group_member_supplies_photo_charge_and_booking_date(self):
        self.track()
        member = {"hash": self.person, "name": "Fixture Person",
                  "booking_date": "01/01/2000",
                  "source_url": "https://source.example/detail/0",
                  "image_url": "https://source.example/photo/0.jpg",
                  "charge": "Grouped charge", "caption": "Original booking caption"}
        key = "group-1"
        grouped.create(self.conn, key, self.destination, self.source,
                       [member], grouped.render_caption([member]))
        grouped.mark_published(self.conn, key, self.destination, "123_789", ["456"])
        changed = self.confirm_departure()
        report = self.publish(changed + timedelta(hours=1))
        self.assertEqual(report["published"], 1)
        self.client.publish_photo.assert_called_once()
        caption = self.client.publish_photo.call_args.args[1]
        self.assertIn("Charges at booking: Grouped charge.", caption)
        self.assertIn("Booked 01/01/2000.", caption)

    def test_stale_departure_is_not_announced(self):
        self.track(image_url="https://source.example/photo/0.jpg")
        self.confirm_departure()
        report = self.publish(self.now + timedelta(hours=96))
        self.assertEqual(report["published"], 0)
        self.assertEqual(report["planned"], 0)
        self.client.publish_photo.assert_not_called()
        self.client.publish_message.assert_not_called()
        count = self.conn.execute("SELECT COUNT(*) FROM release_posts").fetchone()[0]
        self.assertEqual(count, 0)

    def test_uncertain_outcome_is_held_and_never_retried(self):
        self.track(image_url="https://source.example/photo/0.jpg")
        changed = self.confirm_departure()
        self.client.publish_photo.side_effect = FacebookDeliveryUnknown("response lost")
        report = self.publish(changed + timedelta(hours=1))
        self.assertEqual(report["published"], 0)
        self.assertEqual(len(report["errors"]), 1)
        row = self.conn.execute(
            "SELECT status FROM release_posts WHERE record_hash = ?",
            (self.person,)).fetchone()
        self.assertEqual(row, ("uncertain",))
        self.client.publish_photo.side_effect = None
        again = self.publish(changed + timedelta(hours=2))
        self.assertEqual(again["published"], 0)
        self.assertEqual(self.client.publish_photo.call_count, 1)

    def test_preview_plans_without_posting_or_ledger_rows(self):
        self.track(image_url="https://source.example/photo/0.jpg")
        changed = self.confirm_departure()
        report = release_posts.publish_pending(
            self.conn, self.client, self.city, self.destination,
            now=changed + timedelta(hours=1), dry_run=True)
        self.assertEqual(report["planned"], 1)
        self.assertEqual(report["published"], 0)
        self.client.publish_photo.assert_not_called()
        self.client.publish_message.assert_not_called()
        count = self.conn.execute("SELECT COUNT(*) FROM release_posts").fetchone()[0]
        self.assertEqual(count, 0)

    def test_caption_rendering_and_validation(self):
        caption = release_posts.render_release_caption(
            {"name": "Fixture Person", "charge": "Example charge",
             "booking_date": "01/01/2000"},
            datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
        self.assertIn("RELEASED — ALLEN COUNTY JAIL", caption)
        self.assertIn("Fixture Person is no longer listed", caption)
        self.assertIn("Booked 01/01/2000. Charges at booking: Example charge.", caption)
        self.assertTrue(caption.endswith(release_posts.DISCLAIMER))
        with self.assertRaises(ValueError):
            release_posts.render_release_caption(
                {"name": "Fixture Person"}, datetime(2026, 10, 28, 12))
        fallback = release_posts.render_release_caption(
            {"name": " "}, datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
        self.assertIn("A former inmate is no longer listed", fallback)


if __name__ == "__main__":
    unittest.main()
