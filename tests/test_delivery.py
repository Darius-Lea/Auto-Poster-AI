import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        delivery.initialize(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_same_record_can_reach_each_destination(self):
        self.assertTrue(delivery.claim(self.conn, "record", "queue:fort-wayne"))
        delivery.complete(self.conn, "record", "queue:fort-wayne", "outbox/file.json")
        self.assertFalse(delivery.claim(self.conn, "record", "queue:fort-wayne"))
        self.assertTrue(delivery.claim(self.conn, "record", "meta:123"))
        self.assertTrue(delivery.claim(self.conn, "record", "meta:456"))

    def test_pending_and_uncertain_deliveries_block_retries(self):
        delivery.claim(self.conn, "record", "meta:123")
        self.assertTrue(delivery.blocked(self.conn, "record", "meta:123"))
        self.assertFalse(delivery.claim(self.conn, "record", "meta:123"))
        delivery.uncertain(self.conn, "record", "meta:123")
        self.assertFalse(delivery.claim(self.conn, "record", "meta:123"))
        delivery.release(self.conn, "record", "meta:123")
        self.assertTrue(delivery.blocked(self.conn, "record", "meta:123"))

    def test_definite_failure_releases_pending_delivery(self):
        delivery.claim(self.conn, "record", "meta:123")
        delivery.release(self.conn, "record", "meta:123")
        self.assertFalse(delivery.blocked(self.conn, "record", "meta:123"))
        self.assertTrue(delivery.claim(self.conn, "record", "meta:123"))

    def test_confirmed_publish_keeps_remote_reference(self):
        delivery.claim(self.conn, "record", "meta:123")
        delivery.complete(self.conn, "record", "meta:123", "123_987")
        delivery.release(self.conn, "record", "meta:123")
        self.assertEqual(self.conn.execute(
            "SELECT status, remote_ref FROM deliveries"
        ).fetchone(), ("published", "123_987"))
        self.assertEqual(delivery.list_unresolved(self.conn), [])

    def test_resolve_accepted_upload_stores_reference(self):
        delivery.claim(self.conn, "record", "meta:123")
        delivery.uncertain(self.conn, "record", "meta:123")
        unresolved = delivery.list_unresolved(self.conn)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["status"], "uncertain")
        delivery.resolve(self.conn, "record", "meta:123", remote_ref="123_987")
        self.assertTrue(delivery.blocked(self.conn, "record", "meta:123"))
        self.assertEqual(delivery.list_unresolved(self.conn), [])

    def test_resolve_verified_missing_upload_allows_retry(self):
        delivery.claim(self.conn, "record", "meta:123")
        delivery.resolve(self.conn, "record", "meta:123", retry=True)
        self.assertTrue(delivery.claim(self.conn, "record", "meta:123"))

    def test_resolve_requires_exactly_one_choice_and_unresolved_record(self):
        delivery.claim(self.conn, "record", "meta:123")
        for kwargs in ({}, {"remote_ref": "id", "retry": True}, {"remote_ref": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                delivery.resolve(self.conn, "record", "meta:123", **kwargs)
        delivery.complete(self.conn, "record", "meta:123", "id")
        with self.assertRaises(ValueError):
            delivery.resolve(self.conn, "record", "meta:123", retry=True)
        with self.assertRaises(ValueError):
            delivery.resolve(self.conn, "missing", "meta:123", retry=True)

    def test_legacy_seen_table_is_preserved_but_does_not_block_new_destination(self):
        self.conn.execute("CREATE TABLE seen (id_hash TEXT PRIMARY KEY)")
        self.conn.execute("INSERT INTO seen VALUES ('record')")
        delivery.initialize(self.conn)
        self.assertTrue(delivery.claim(self.conn, "record", "meta:123"))
        self.assertEqual(self.conn.execute("SELECT id_hash FROM seen").fetchone(), ("record",))

    def test_claim_survives_reconnection_and_only_one_connection_wins(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "deliveries.db"
            with sqlite3.connect(path) as first, sqlite3.connect(path) as second:
                delivery.initialize(first)
                self.assertTrue(delivery.claim(first, "record", "meta:123"))
                self.assertFalse(delivery.claim(second, "record", "meta:123"))
            with sqlite3.connect(path) as restored:
                self.assertTrue(delivery.blocked(restored, "record", "meta:123"))

    def test_empty_identity_and_confirmation_are_rejected(self):
        with self.assertRaises(ValueError):
            delivery.claim(self.conn, "", "meta:123")
        delivery.claim(self.conn, "record", "meta:123")
        with self.assertRaises(ValueError):
            delivery.complete(self.conn, "record", "meta:123", "")


if __name__ == "__main__":
    unittest.main()
