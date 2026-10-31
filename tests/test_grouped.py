from datetime import datetime, timezone
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import grouped


class GroupedTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        grouped.initialize(self.conn)
        self.destination = "meta:123:fort-wayne"
        self.members = [
            {"hash": f"hash-{index}", "name": f"Fixture {index}", "booking_date": "01/01/2000",
             "source_url": f"https://source.example/detail/{index}",
             "image_url": f"https://source.example/photo/{index}.jpg",
             "caption": f"Original caption {index}."}
            for index in range(2)
        ]

    def create(self, key="group-1", destination=None, members=None):
        return grouped.create(self.conn, key, destination or self.destination, "source",
                              members if members is not None else self.members, "Original group caption")

    def publish(self, key="group-1"):
        grouped.mark_published(self.conn, key, self.destination, "123_456", ["10", "11"])

    def test_group_is_reserved_with_immutable_original_metadata(self):
        self.assertTrue(self.create())
        self.members[0]["caption"] = "Caller changed it"
        stored = grouped.get(self.conn, "group-1", self.destination)
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["members"][0]["caption"], "Original caption 0.")
        self.assertEqual(stored["original_caption"], "Original group caption")
        self.assertEqual(stored["photo_ids"], [])

    def test_confirmed_group_maps_each_member_to_its_photo_and_parent_post(self):
        self.create()
        grouped.mark_published(self.conn, "group-1", self.destination, "meta:123_456", ["meta:10", "11"])
        stored = grouped.get(self.conn, "group-1", self.destination)
        self.assertEqual(stored["status"], "published")
        self.assertEqual(stored["post_id"], "123_456")
        self.assertEqual([member["photo_id"] for member in stored["members"]], ["10", "11"])
        self.assertEqual(stored["photo_ids"], ["10", "11"])
        self.assertEqual(stored["members"][1]["caption"], self.members[1]["caption"])
        self.assertFalse(self.create())

    def test_existing_groups_never_restart_automatically(self):
        for index, status in enumerate(("pending", "uncertain", "published", "failed")):
            with self.subTest(status=status):
                key = f"group-{index}"
                self.create(key)
                if status == "uncertain":
                    grouped.mark_uncertain(self.conn, key, self.destination, "Lost response")
                elif status == "published":
                    self.publish(key)
                elif status == "failed":
                    grouped.fail(self.conn, key, self.destination, "Rejected")
                self.assertFalse(self.create(key))
                self.assertEqual(grouped.get(self.conn, key, self.destination)["status"], status)

    def test_different_original_content_cannot_overwrite_same_identity(self):
        self.create()
        altered = copy.deepcopy(self.members)
        altered[0]["name"] = "Different person"
        with self.assertRaisesRegex(ValueError, "different original"):
            self.create(members=altered)
        with self.assertRaises(ValueError):
            grouped.create(self.conn, "group-1", self.destination, "source", self.members, "Changed caption")
        self.assertEqual(grouped.get(self.conn, "group-1", self.destination)["members"], self.members)

    def test_only_definite_failure_can_be_explicitly_retried(self):
        self.create()
        grouped.fail(self.conn, "group-1", self.destination, "HTTP 400 rejected")
        grouped.retry_failed(self.conn, "group-1", self.destination)
        self.assertEqual(grouped.get(self.conn, "group-1", self.destination)["status"], "pending")
        grouped.mark_uncertain(self.conn, "group-1", self.destination, "Feed response lost")
        with self.assertRaises(ValueError):
            grouped.retry_failed(self.conn, "group-1", self.destination)
        with self.assertRaises(ValueError):
            self.publish()
        with self.assertRaises(ValueError):
            grouped.fail(self.conn, "group-1", self.destination, "Must not erase ambiguity")

    def test_bad_or_incomplete_photo_mapping_is_not_confirmed(self):
        self.create()
        for post_id, photos in [("123_456", ["10"]), ("123_456", ["10", "10"]),
                                ("wrong-id", ["10", "11"]), ("123_456", ["bad", "11"])]:
            with self.subTest(post_id=post_id, photos=photos), self.assertRaises(ValueError):
                grouped.mark_published(self.conn, "group-1", self.destination, post_id, photos)
        self.assertEqual(grouped.get(self.conn, "group-1", self.destination)["status"], "pending")

    def test_group_identity_is_scoped_to_destination(self):
        self.assertTrue(self.create())
        self.assertTrue(self.create(destination="meta:999:fort-wayne"))
        self.publish()
        self.assertEqual(len(grouped.list_groups(self.conn, self.destination, "published")), 1)
        self.assertEqual(grouped.list_groups(self.conn, "meta:999:fort-wayne", "published"), [])

    def test_record_lookup_returns_only_published_groups_for_its_page(self):
        self.create()
        self.assertEqual(grouped.groups_for_record(self.conn, "hash-0", self.destination), [])
        self.publish()
        self.assertEqual(grouped.groups_for_record(self.conn, "hash-0", self.destination)[0]["post_id"], "123_456")
        self.assertEqual(grouped.groups_for_record(self.conn, "missing", self.destination), [])
        self.assertEqual(grouped.groups_for_record(self.conn, "hash-0", "meta:999:fort-wayne"), [])

    def test_member_validation_and_ten_person_limit(self):
        ten = [dict(self.members[0], hash=f"hash-{index}") for index in range(10)]
        self.assertTrue(self.create("ten", members=ten))
        for invalid in [[], ten + [dict(ten[0], hash="eleven")], [self.members[0], self.members[0]],
                        [{"hash": "missing-other-data"}]]:
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                self.create("invalid", members=invalid)

    def test_local_caption_only_shows_status_for_removed_member_and_keeps_original_data(self):
        original = copy.deepcopy(self.members)
        caption = grouped.render_caption(self.members, {"hash-0": "no_longer_listed"},
                                         datetime(2026, 10, 28, 12, 17, tzinfo=timezone.utc))
        self.assertTrue(caption.startswith(grouped.TITLE))
        self.assertIn(f"1. Fixture 0\n{grouped.STATUS_NOTE}", caption)
        self.assertNotIn("Original caption 0.", caption)
        self.assertNotIn("2. Fixture 1", caption)
        self.assertNotIn("Original caption 1.", caption)
        self.assertEqual(caption.count(grouped.STATUS_NOTE), 1)
        self.assertIn("08:17 AM EDT", caption)
        self.assertTrue(caption.endswith(grouped.DISCLAIMER))
        self.assertEqual(self.members, original)
        restored = grouped.render_caption(self.members, {"hash-0": "listed"})
        self.assertNotIn(grouped.STATUS_NOTE, restored)
        self.assertNotIn("Fixture 0", restored)

    def test_fallback_caption_omits_source_and_member_details_and_rejects_unknown_status(self):
        member = dict(self.members[0])
        member.pop("caption")
        member["charge"] = "Example allegation"
        caption = grouped.render_caption([member])
        self.assertTrue(caption.startswith(grouped.TITLE))
        self.assertNotIn(member["source_url"], caption)
        self.assertNotIn(member["booking_date"], caption)
        self.assertNotIn(member["charge"], caption)
        self.assertNotIn(member["name"], caption)
        with self.assertRaises(ValueError):
            grouped.render_caption([member], {member["hash"]: "released"})
        with self.assertRaises(ValueError):
            grouped.render_caption([member], checked_at=datetime(2026, 10, 28))

    def test_pending_reservation_survives_database_reconnection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.db"
            with sqlite3.connect(path) as conn:
                grouped.initialize(conn)
                self.assertTrue(grouped.create(conn, "persisted", self.destination, "source", self.members, "Caption"))
            with sqlite3.connect(path) as conn:
                grouped.initialize(conn)
                self.assertFalse(grouped.create(conn, "persisted", self.destination, "source", self.members, "Caption"))
                self.assertEqual(grouped.get(conn, "persisted", self.destination)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
