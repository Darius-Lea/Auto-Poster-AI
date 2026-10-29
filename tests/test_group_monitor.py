from datetime import datetime, timedelta, timezone
import sqlite3
import unittest
from unittest.mock import Mock, patch

from core import custody, grouped, group_monitor


class GroupMonitorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        custody.initialize(self.conn)
        grouped.initialize(self.conn)
        self.destination = "meta:123:fort-wayne"
        self.source = "allen-roster"
        self.url = "https://source.example/roster"
        self.now = datetime(2026, 10, 28, 12, 17, tzinfo=timezone.utc)
        self.members = [
            {"hash": f"hash-{index}", "name": f"Fixture {index}", "booking_date": "01/01/2000",
             "source_url": f"https://source.example/detail/{index}",
             "image_url": f"https://source.example/photo/{index}", "caption": f"Original {index}."}
            for index in range(2)
        ]
        self.original = "Original grouped booking captions."
        grouped.create(self.conn, "group-1", self.destination, self.source, self.members, self.original)
        grouped.mark_published(self.conn, "group-1", self.destination, "123_456", ["10", "11"])
        for index, member in enumerate(self.members):
            custody.track(self.conn, member["hash"], self.destination, self.source,
                          str(10 + index), member["caption"], member["source_url"], member["name"])
        self.captions = {"123_456": self.original}
        self.client = Mock()
        self.client.get_caption.side_effect = lambda reference: self.captions[reference]
        self.client.update_caption.side_effect = self.edit

    def edit(self, reference, caption, *, expected_caption):
        self.assertEqual(self.captions[reference], expected_caption)
        self.captions[reference] = caption

    def status(self, index, status, day=0):
        self.conn.execute(
            "UPDATE custody_posts SET desired_status=?,status_changed_at=? WHERE record_hash=? AND destination=?",
            (status, (self.now + timedelta(days=day)).isoformat(), f"hash-{index}", self.destination),
        )
        self.conn.commit()

    def run_sync(self, day=0, source=None, destination=None):
        return group_monitor.sync(self.conn, self.client, source or self.source,
                                  destination or self.destination, self.url,
                                  self.now + timedelta(days=day))

    def test_initial_and_unchanged_listed_groups_do_not_request_facebook(self):
        self.assertEqual(self.run_sync(), {"updated": 0, "errors": []})
        self.assertEqual(self.run_sync(1), {"updated": 0, "errors": []})
        self.client.get_caption.assert_not_called()
        self.client.update_caption.assert_not_called()

    def test_missing_person_is_scoped_to_their_entry_and_group_post_is_retained(self):
        self.status(0, "no_longer_listed")
        self.assertEqual(self.run_sync(), {"updated": 1, "errors": []})
        caption = self.captions["123_456"]
        self.assertTrue(caption.startswith(self.original))
        self.assertIn("Fixture 0 (booking 01/01/2000): no longer listed", caption)
        self.assertNotIn("Fixture 1 (booking", caption)
        self.assertIn("08:17 AM EDT", caption)
        self.client.publish_group.assert_not_called()
        self.client.publish_photo.assert_not_called()
        self.client.delete.assert_not_called()
        self.assertEqual(grouped.get(self.conn, "group-1", self.destination)["original_caption"], self.original)
        reads = self.client.get_caption.call_count
        self.assertEqual(self.run_sync(1)["updated"], 0)
        self.assertEqual(self.client.get_caption.call_count, reads)

    def test_reappearance_removes_only_exact_note_preserving_editor_text(self):
        self.status(0, "no_longer_listed")
        self.run_sync()
        self.captions["123_456"] += "\nEditor correction after the status note."
        self.status(0, "listed", 1)
        self.assertEqual(self.run_sync(1), {"updated": 1, "errors": []})
        self.assertEqual(self.captions["123_456"], self.original + "\nEditor correction after the status note.")

    def test_two_people_changes_replaces_footer_instead_of_duplicating_it(self):
        self.status(0, "no_longer_listed")
        self.run_sync()
        self.status(1, "no_longer_listed", 1)
        self.assertEqual(self.run_sync(1)["updated"], 1)
        caption = self.captions["123_456"]
        self.assertEqual(caption.count(group_monitor.HEADER), 1)
        self.assertIn("Fixture 0 (booking", caption)
        self.assertIn("Fixture 1 (booking", caption)
        self.status(0, "listed", 2)
        self.run_sync(2)
        caption = self.captions["123_456"]
        self.assertNotIn("Fixture 0 (booking", caption)
        self.assertIn("Fixture 1 (booking", caption)

    def test_failed_remote_edit_retries_without_advancing_note_checkpoint(self):
        self.status(0, "no_longer_listed")
        self.client.update_caption.side_effect = RuntimeError("Edit rejected")
        self.assertTrue(self.run_sync()["errors"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM booking_group_notes").fetchone()[0], 0)
        self.assertEqual(self.captions["123_456"], self.original)
        self.client.update_caption.side_effect = self.edit
        self.assertEqual(self.run_sync(1)["updated"], 1)
        self.assertEqual(self.client.update_caption.call_count, 2)

    def test_lost_local_ack_is_recovered_without_another_remote_edit(self):
        self.status(0, "no_longer_listed")
        with patch("core.group_monitor._checkpoint", side_effect=sqlite3.OperationalError("Local write failed")):
            self.assertTrue(self.run_sync()["errors"])
        self.assertIn(group_monitor.HEADER, self.captions["123_456"])
        self.assertEqual(self.client.update_caption.call_count, 1)
        self.assertEqual(self.run_sync(1), {"updated": 1, "errors": []})
        self.assertEqual(self.client.update_caption.call_count, 1)
        self.assertEqual(self.run_sync(2)["updated"], 0)

    def test_lost_removal_ack_does_not_restore_or_duplicate_any_text(self):
        self.status(0, "no_longer_listed")
        self.run_sync()
        self.status(0, "listed", 1)
        with patch("core.group_monitor._checkpoint", side_effect=sqlite3.OperationalError("Local write failed")):
            self.assertTrue(self.run_sync(1)["errors"])
        self.assertEqual(self.captions["123_456"], self.original)
        edits = self.client.update_caption.call_count
        self.assertEqual(self.run_sync(2)["updated"], 1)
        self.assertEqual(self.client.update_caption.call_count, edits)

    def test_foreign_source_and_destination_are_not_read_or_edited(self):
        self.status(0, "no_longer_listed")
        self.assertEqual(self.run_sync(source="different-roster"), {"updated": 0, "errors": []})
        self.assertEqual(self.run_sync(destination="meta:999:fort-wayne"), {"updated": 0, "errors": []})
        self.client.get_caption.assert_not_called()
        self.client.update_caption.assert_not_called()

    def test_edited_or_missing_managed_note_requires_review(self):
        self.status(0, "no_longer_listed")
        self.run_sync()
        previous = self.conn.execute("SELECT last_note FROM booking_group_notes").fetchone()[0]
        self.captions["123_456"] = self.captions["123_456"].replace("Roster checked:", "Edited date:")
        self.status(0, "listed", 1)
        edits = self.client.update_caption.call_count
        result = self.run_sync(1)
        self.assertEqual(result["updated"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(self.client.update_caption.call_count, edits)
        self.assertEqual(self.conn.execute("SELECT last_note FROM booking_group_notes").fetchone()[0], previous)

    def test_missing_custody_metadata_is_not_treated_as_reappearance(self):
        self.status(0, "no_longer_listed")
        self.run_sync()
        self.conn.execute("DELETE FROM custody_posts WHERE record_hash='hash-0'")
        self.conn.commit()
        edits = self.client.update_caption.call_count
        result = self.run_sync(1)
        self.assertTrue(result["errors"])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(self.client.update_caption.call_count, edits)


if __name__ == "__main__":
    unittest.main()
