from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from core import custody


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        custody.initialize(self.conn)
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.members = {'person', *[f'other-{index}' for index in range(9)]}
        self.absent = self.members - {'person'}
        custody.track(self.conn, 'person', 'meta:123:fort-wayne', 'allen',
                      'meta:123_456', 'Original booking caption', 'https://source.example/detail/1', 'Fixture')

    def observe(self, members, days=0):
        return custody.observe(self.conn, 'allen', members, self.now + timedelta(days=days))

    def state(self):
        return (self.conn.execute('SELECT * FROM custody_posts').fetchall(),
                self.conn.execute('SELECT * FROM custody_scans').fetchall())

    def missing_candidate(self):
        self.observe(self.members)
        self.assertEqual(self.observe(self.absent, 1), [])
        candidates = self.observe(self.absent, 2)
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def test_requires_two_daily_absences_and_never_infers_release(self):
        candidate = self.missing_candidate()
        self.assertEqual(candidate['target_status'], 'no_longer_listed')
        self.assertEqual(candidate['original_caption'], 'Original booking caption')
        self.assertEqual(candidate['remote_ref'], '123_456')
        self.assertEqual(candidate['source_url'], 'https://source.example/detail/1')
        self.assertEqual(candidate['missing_since'], (self.now + timedelta(days=1)).isoformat())

    def test_too_frequent_scans_do_not_advance_missing_count(self):
        self.observe(self.members)
        self.observe(self.absent, 1)
        before = self.state()
        self.assertEqual(custody.observe(self.conn, 'allen', self.absent,
                                        self.now + timedelta(days=1, hours=19)), [])
        self.assertEqual(self.state(), before)

    def test_empty_and_suspiciously_smaller_scans_change_nothing(self):
        self.observe(self.members)
        for members in [set(), set(list(self.members)[:7])]:
            before = self.state()
            with self.assertRaises(ValueError):
                self.observe(members, 1)
            self.assertEqual(self.state(), before)

    def test_exact_twenty_percent_drop_is_allowed(self):
        self.observe(self.members)
        self.observe(set(list(self.members)[:8]), 1)
        self.assertEqual(self.conn.execute('SELECT member_count FROM custody_scans').fetchone()[0], 8)

    def test_unapplied_edits_retry_and_success_stops_retrying(self):
        candidate = self.missing_candidate()
        self.assertEqual(self.observe(self.absent, 3)[0]['target_status'], 'no_longer_listed')
        custody.mark_applied(self.conn, candidate['record_hash'], candidate['destination'],
                             candidate['target_status'], self.now + timedelta(days=3))
        self.assertEqual(self.observe(self.absent, 4), [])

    def test_reappearance_restores_original_caption(self):
        candidate = self.missing_candidate()
        custody.mark_applied(self.conn, candidate['record_hash'], candidate['destination'],
                             'no_longer_listed', self.now + timedelta(days=2))
        restored = self.observe(self.members, 3)
        self.assertEqual(restored[0]['target_status'], 'listed')
        self.assertEqual(restored[0]['original_caption'], 'Original booking caption')
        custody.mark_applied(self.conn, 'person', 'meta:123:fort-wayne', 'listed',
                             self.now + timedelta(days=3))
        self.assertEqual(self.observe(self.members, 4), [])

    def test_reappearance_before_edit_cancels_pending_change(self):
        self.missing_candidate()
        self.assertEqual(self.observe(self.members, 3), [])
        with self.assertRaises(ValueError):
            custody.mark_applied(self.conn, 'person', 'meta:123:fort-wayne',
                                 'no_longer_listed', self.now + timedelta(days=3))

    def test_tracking_again_keeps_original_caption_and_status_history(self):
        self.missing_candidate()
        before = self.conn.execute('SELECT missing_checks, desired_status FROM custody_posts').fetchone()
        custody.track(self.conn, 'person', 'meta:123:fort-wayne', 'allen', '123_456',
                      'Replacement must not overwrite history', 'https://source.example/detail/1', 'Fixture')
        after = self.conn.execute('SELECT missing_checks, desired_status FROM custody_posts').fetchone()
        self.assertEqual(after, before)
        self.assertEqual(custody.pending_updates(self.conn, 'allen')[0]['original_caption'], 'Original booking caption')

    def test_scopes_are_isolated_and_non_meta_or_unconfirmed_refs_rejected(self):
        self.observe(self.members)
        self.assertEqual(custody.observe(self.conn, 'other-source', {'something'}, self.now), [])
        self.assertEqual(self.conn.execute('SELECT missing_checks FROM custody_posts').fetchone()[0], 0)
        for destination, reference in [('queue:fort-wayne', 'file.json'), ('meta:123:fort-wayne', '')]:
            with self.assertRaises(ValueError):
                custody.track(self.conn, 'other', destination, 'allen', reference, 'caption', 'https://example/detail')
        with self.assertRaises(ValueError):
            custody.track(self.conn, 'person', 'meta:123:fort-wayne', 'allen', '123_999', 'caption', 'https://example/detail')

    def test_naive_times_and_unsupported_status_are_rejected(self):
        with self.assertRaises(ValueError):
            custody.observe(self.conn, 'allen', self.members, datetime(2026, 10, 28))
        with self.assertRaises(ValueError):
            custody.mark_applied(self.conn, 'person', 'meta:123:fort-wayne', 'released', self.now)


if __name__ == '__main__':
    unittest.main()
