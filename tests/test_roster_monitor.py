from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import unittest
from unittest.mock import Mock, patch

from core import custody, delivery, grouped, group_monitor
from core.roster_monitor import STATUS_NOTE, reconcile_city, source_key


def hash_fields(name, booked, url):
    return hashlib.sha256(f'{name}|{booked}|{url}'.lower().encode()).hexdigest()


class RosterMonitorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        delivery.initialize(self.conn)
        custody.initialize(self.conn)
        self.city = {'city': 'Fort Wayne', 'workspace_key': 'fort-wayne', 'sources': [
            {'adapter': 'allen_sheriff', 'kind': 'booking', 'url': 'https://source.example/roster'}
        ]}
        self.destination = 'meta:123:fort-wayne'
        self.source = source_key(self.city, self.city['sources'][0])
        self.rows = [{'name': f'Synthetic Fixture {index}', 'booking_date': '01/01/2000',
                      'detail_url': f'https://source.example/detail/{index}'} for index in range(5)]
        self.person = hash_fields(self.rows[0]['name'], self.rows[0]['booking_date'], self.rows[0]['detail_url'])
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.original = (
            "Allen County booking update for Fort Wayne, New Haven, Huntertown, Leo-Cedarville, "
            "Grabill, Woodburn, and nearby communities.\n\n"
            "Original booking information. Presumed innocent.\n\n"
            "Follow this Page for Allen County booking and roster-status updates.\n\n"
            "#FortWayne #AllenCounty"
        )
        self.captions = {'123_456': self.original}
        self.client = Mock()
        self.client.get_caption.side_effect = lambda ref: self.captions[ref.removeprefix('meta:')]
        self.client.update_caption.side_effect = self.apply_caption
        self.adapter = Mock(snapshot_complete=True)
        self.adapter.snapshot_rows.return_value = self.rows
        patcher = patch('core.roster_monitor.get_adapter', return_value=self.adapter)
        self.get_adapter = patcher.start()
        self.addCleanup(patcher.stop)
        self.track()

    def track(self, destination=None, remote_ref='meta:123_456'):
        custody.track(self.conn, self.person, destination or self.destination, self.source,
                      remote_ref, self.original, self.rows[0]['detail_url'], self.rows[0]['name'])

    def apply_caption(self, reference, caption, **kwargs):
        self.captions[reference.removeprefix('meta:')] = caption

    def run_day(self, day, *, absent=False, dry_run=False):
        self.adapter.snapshot_rows.return_value = self.rows[1:] if absent else self.rows
        return reconcile_city(self.city, self.conn, self.client, hash_fields, self.destination,
                              now=self.now + timedelta(days=day), dry_run=dry_run)

    def state(self):
        return tuple(self.conn.iterdump())

    def prepare_first_absence(self):
        self.assertEqual(self.run_day(0)['updated'], 0)
        self.assertEqual(self.run_day(1, absent=True)['updated'], 0)

    def test_disappearance_edits_person_caption_and_reappearance_removes_managed_note(self):
        self.prepare_first_absence()
        report = self.run_day(2, absent=True)
        self.assertEqual(report, {'updated': 1, 'planned': 1, 'errors': []})
        self.client.update_caption.assert_called_once()
        self.assertTrue(self.captions['123_456'].startswith(self.original))
        self.assertIn(STATUS_NOTE, self.captions['123_456'])
        self.assertIn('Roster checked:', self.captions['123_456'])
        self.assertIn('UPDATE: (RELEASED)', self.captions['123_456'])
        self.assertIn('Allen County Jail (260) 449-7376', self.captions['123_456'])
        self.assertEqual(self.run_day(3, absent=True)['updated'], 0)
        self.assertEqual(self.client.update_caption.call_count, 1)
        # An editor's changes to the booking remain when the roster listing returns.
        self.captions['123_456'] = self.captions['123_456'].replace(self.original, 'Editor corrected booking text.')
        self.assertEqual(self.run_day(4)['updated'], 1)
        self.assertTrue(self.captions['123_456'].endswith('Editor corrected booking text.'))
        self.assertIn('Fort Wayne, New Haven, Huntertown', self.captions['123_456'])
        self.assertNotIn(STATUS_NOTE, self.captions['123_456'])
        self.client.publish_photo.assert_not_called()
        self.client.delete.assert_not_called()

    def test_incomplete_empty_and_failed_snapshots_do_not_advance_history(self):
        self.run_day(0)
        before = self.state()
        for complete, rows, failure in [(False, self.rows[1:], None), (True, [], None),
                                        (True, self.rows[1:], RuntimeError('source unavailable'))]:
            with self.subTest(complete=complete, empty=not rows, failure=failure):
                self.adapter.snapshot_complete = complete
                self.adapter.snapshot_rows.side_effect = failure
                self.adapter.snapshot_rows.return_value = rows
                report = reconcile_city(self.city, self.conn, self.client, hash_fields,
                                        self.destination, now=self.now + timedelta(days=1))
                self.assertTrue(report['errors'])
                self.assertEqual(self.state(), before)
        self.client.update_caption.assert_not_called()

    def test_reappearance_removes_status_note_even_after_editor_appends_text(self):
        self.prepare_first_absence()
        self.assertEqual(self.run_day(2, absent=True)['updated'], 1)
        self.captions['123_456'] += '\nAn editor added a correction.'
        self.assertEqual(self.run_day(3)['updated'], 1)
        self.assertEqual(self.captions['123_456'], self.original + '\nAn editor added a correction.')
        self.assertNotIn(STATUS_NOTE, self.captions['123_456'])

    def test_reappearance_does_not_acknowledge_an_unrecognized_edited_status_note(self):
        self.prepare_first_absence()
        self.run_day(2, absent=True)
        self.captions['123_456'] = self.captions['123_456'].replace('Roster checked:', 'Last check:')
        result = self.run_day(3)
        self.assertEqual(result['updated'], 0)
        self.assertTrue(result['errors'])
        self.assertEqual(self.conn.execute('SELECT applied_status FROM custody_posts').fetchone()[0],
                         'no_longer_listed')

    def test_failed_edit_is_retried_on_next_valid_daily_scan(self):
        self.prepare_first_absence()
        self.client.update_caption.side_effect = RuntimeError('Facebook rejected edit')
        report = self.run_day(2, absent=True)
        self.assertTrue(report['errors'])
        self.assertEqual(report['updated'], 0)
        self.assertEqual(self.conn.execute('SELECT applied_status FROM custody_posts').fetchone()[0], 'listed')
        self.client.update_caption.side_effect = self.apply_caption
        self.assertEqual(self.run_day(3, absent=True)['updated'], 1)
        self.assertEqual(self.client.update_caption.call_count, 2)
        self.assertEqual(self.run_day(4, absent=True)['updated'], 0)

    def test_preview_uses_database_copy_and_never_edits_existing_post(self):
        self.prepare_first_absence()
        before = self.state()
        report = self.run_day(2, absent=True, dry_run=True)
        self.assertEqual(report, {'updated': 0, 'planned': 1, 'errors': []})
        self.assertEqual(self.state(), before)
        self.assertEqual(self.captions['123_456'], self.original)
        self.client.update_caption.assert_not_called()
        # Preview did not consume the real daily check.
        self.assertEqual(self.run_day(2, absent=True)['updated'], 1)

    def test_published_legacy_receipt_is_imported_from_matching_current_roster(self):
        self.conn.execute('DELETE FROM custody_posts')
        self.conn.commit()
        delivery.claim(self.conn, self.person, self.destination)
        delivery.complete(self.conn, self.person, self.destination, 'meta:123_456')
        report = self.run_day(0)
        self.assertEqual(report['errors'], [])
        self.client.get_caption.assert_called_once_with('meta:123_456')
        self.assertEqual(self.conn.execute(
            'SELECT original_caption, remote_ref, source_url FROM custody_posts'
        ).fetchone(), (self.original, '123_456', self.rows[0]['detail_url']))
        self.client.update_caption.assert_not_called()

    def test_other_page_deliveries_are_not_edited(self):
        self.conn.execute('DELETE FROM custody_posts')
        self.conn.commit()
        self.track(destination='meta:999:fort-wayne', remote_ref='meta:999_456')
        self.prepare_first_absence()
        report = self.run_day(2, absent=True)
        self.assertEqual(report, {'updated': 0, 'planned': 0, 'errors': []})
        self.client.get_caption.assert_not_called()
        self.client.update_caption.assert_not_called()

    def test_edit_acknowledgement_failure_does_not_duplicate_status_note_or_remote_edit(self):
        self.prepare_first_absence()
        original_mark = custody.mark_applied
        with patch('core.roster_monitor.custody.mark_applied', side_effect=RuntimeError('local write failed')):
            report = self.run_day(2, absent=True)
        self.assertTrue(report['errors'])
        self.assertEqual(self.captions['123_456'].count(STATUS_NOTE), 1)
        self.assertEqual(self.client.update_caption.call_count, 1)
        self.assertEqual(self.run_day(3, absent=True)['updated'], 1)
        self.assertEqual(self.captions['123_456'].count(STATUS_NOTE), 1)
        self.assertEqual(self.client.update_caption.call_count, 1)
        self.assertIs(custody.mark_applied, original_mark)

    def add_group(self):
        grouped.initialize(self.conn)
        members = [{
            'hash': self.person, 'name': self.rows[0]['name'],
            'booking_date': self.rows[0]['booking_date'],
            'source_url': self.rows[0]['detail_url'],
            'image_url': 'https://source.example/photo/0', 'caption': self.original,
        }]
        grouped.create(self.conn, 'group-1', self.destination, self.source,
                       members, 'Original grouped booking text.')
        grouped.mark_published(self.conn, 'group-1', self.destination, '123_789', ['456'])
        # Grouped deliveries track the member photo, while the feed post has a
        # separate caption. This mirrors the catch-up engine's actual ledger.
        self.conn.execute('UPDATE custody_posts SET remote_ref=? WHERE record_hash=?',
                          ('456', self.person))
        self.conn.commit()
        self.captions['456'] = self.original
        self.captions['123_789'] = 'Original grouped booking text.'

    def test_grouped_booking_updates_only_person_photo_caption(self):
        self.add_group()
        self.prepare_first_absence()
        report = self.run_day(2, absent=True)
        self.assertEqual(report, {'updated': 1, 'planned': 1, 'errors': []})
        self.assertIn(STATUS_NOTE, self.captions['456'])
        self.assertEqual(self.captions['123_789'], 'Original grouped booking text.')
        self.assertEqual(self.run_day(3, absent=True)['updated'], 0)
        self.assertEqual(self.run_day(4)['updated'], 1)
        self.assertEqual(self.captions['456'], self.original)
        self.assertEqual(self.captions['123_789'], 'Original grouped booking text.')
        self.client.publish_group.assert_not_called()
        self.client.publish_photo.assert_not_called()
        self.client.delete.assert_not_called()

    def test_group_caption_stays_unchanged_if_member_photo_edit_fails(self):
        self.add_group()
        self.prepare_first_absence()

        def edit(reference, caption, **kwargs):
            if reference == '456':
                raise RuntimeError('Member photo edit rejected')
            self.apply_caption(reference, caption, **kwargs)

        self.client.update_caption.side_effect = edit
        report = self.run_day(2, absent=True)
        self.assertEqual(report['updated'], 0)
        self.assertTrue(report['errors'])
        self.assertEqual(self.captions['456'], self.original)
        self.assertEqual(self.captions['123_789'], 'Original grouped booking text.')
        self.client.update_caption.side_effect = self.apply_caption
        self.assertEqual(self.run_day(3, absent=True)['updated'], 1)
        self.assertIn(STATUS_NOTE, self.captions['456'])

    def test_preview_or_incomplete_roster_does_not_edit_or_checkpoint_group_status(self):
        self.add_group()
        self.prepare_first_absence()
        before = self.state()
        self.run_day(2, absent=True, dry_run=True)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.captions['123_789'], 'Original grouped booking text.')
        self.adapter.snapshot_complete = False
        self.assertTrue(self.run_day(2, absent=True)['errors'])
        self.assertEqual(self.state(), before)
        self.client.update_caption.assert_not_called()


if __name__ == '__main__':
    unittest.main()
