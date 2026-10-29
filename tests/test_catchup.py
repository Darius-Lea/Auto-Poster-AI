from datetime import date
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from core import catchup, custody, delivery, grouped
from core.facebook import FacebookDeliveryUnknown, FacebookError


def hash_fields(name, booked, url):
    return hashlib.sha256(f'{name}|{booked}|{url}'.lower().encode()).hexdigest()


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        delivery.initialize(self.conn)
        custody.initialize(self.conn)
        grouped.initialize(self.conn)
        self.city = {'city': 'Fort Wayne', 'workspace_key': 'fort-wayne', 'logo': 'fixture-logo.png',
                     'sources': [{'adapter': 'allen_sheriff', 'kind': 'booking',
                                  'url': 'https://source.example/roster'}]}
        self.destination = 'meta:123:fort-wayne'
        self.rows = [{'name': f'Synthetic Fixture {index}', 'booking_date': '01/01/2000',
                      'detail_url': f'https://source.example/detail/{index}',
                      'image_url': f'https://source.example/photo/{index}.jpg'} for index in range(23)]
        self.adapter = Mock(snapshot_complete=True)
        self.adapter.snapshot_rows.return_value = self.rows
        self.client = Mock(page_id='123')
        self.next_id = 1000
        self.client.publish_group.side_effect = self.confirm_group
        self.start(patch.object(catchup, 'get_adapter', return_value=self.adapter))
        response = Mock()
        response.text = '<p>Age: 30</p><p>Statute Description: Example charge</p>'
        self.details = self.start(patch.object(catchup.requests, 'get', return_value=response))
        self.download = self.start(patch.object(catchup, 'download_image', return_value='/tmp/nonexistent-catchup-fixture.jpg'))
        self.stamp = self.start(patch.object(catchup.watermark, 'stamp', side_effect=lambda image, logo, output: output))

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def confirm_group(self, items, caption):
        self.next_id += 1
        return {'post_id': f'123_{self.next_id}',
                'photo_ids': [str(self.next_id * 100 + index) for index in range(len(items))]}

    def identity(self, row):
        return hash_fields(row['name'], row['booking_date'], row['detail_url'])

    def run_batch(self, **kwargs):
        return catchup.run_batch(self.city, self.conn, self.client, hash_fields, **kwargs)

    def state(self):
        return tuple(self.conn.iterdump())

    def test_groups_of_ten_skip_existing_individual_and_final_partial_finishes(self):
        first = self.identity(self.rows[0])
        delivery.claim(self.conn, first, self.destination)
        delivery.complete(self.conn, first, self.destination, 'meta:123_999')
        result = self.run_batch(max_groups=2)
        self.assertEqual(result['groups_published'], 2)
        self.assertEqual(result['people_published'], 20)
        self.assertEqual(result['remaining'], 2)
        self.assertFalse(result['complete'])
        self.assertFalse(result['errors'])
        self.assertEqual(self.client.publish_group.call_count, 2)
        self.assertEqual([len(call.args[0]) for call in self.client.publish_group.call_args_list], [10, 10])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM custody_posts').fetchone()[0], 20)
        first_group = grouped.list_groups(self.conn, self.destination, 'published')[0]
        self.assertNotIn(first, [member['hash'] for member in first_group['members']])
        self.assertTrue(all(member.get('photo_id') for member in first_group['members']))
        final = self.run_batch(max_groups=2)
        self.assertEqual(final['people_published'], 2)
        self.assertEqual(final['remaining'], 0)
        self.assertTrue(final['complete'])
        self.assertEqual(len(self.client.publish_group.call_args.args[0]), 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM deliveries WHERE status='published'").fetchone()[0], 23)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM custody_posts').fetchone()[0], 22)
        calls = self.client.publish_group.call_count
        self.assertTrue(self.run_batch()['complete'])
        self.assertEqual(self.client.publish_group.call_count, calls)

    def test_ambiguous_upload_pauses_and_does_not_send_a_second_group_or_retry(self):
        self.client.publish_group.side_effect = FacebookDeliveryUnknown('The upload response was lost')
        result = self.run_batch(max_groups=2)
        self.assertTrue(result['paused'])
        self.assertFalse(result['complete'])
        self.assertTrue(result['errors'])
        self.client.publish_group.assert_called_once()
        self.assertEqual(len(delivery.list_unresolved(self.conn)), 10)
        self.assertEqual(grouped.list_groups(self.conn, self.destination)[0]['status'], 'uncertain')
        self.assertTrue(self.run_batch()['paused'])
        self.client.publish_group.assert_called_once()

    def test_definite_rejection_releases_claims_and_records_failed_group(self):
        self.client.publish_group.side_effect = FacebookError('Facebook rejected this request')
        result = self.run_batch()
        self.assertTrue(result['errors'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['remaining'], 23)
        self.assertEqual(delivery.list_unresolved(self.conn), [])
        self.assertEqual(grouped.list_groups(self.conn, self.destination)[0]['status'], 'failed')
        self.client.publish_group.assert_called_once()
        retry = self.run_batch()
        self.assertTrue(retry['paused'])
        self.client.publish_group.assert_called_once()

    def test_partial_upload_identifiers_survive_failed_or_uncertain_feed_checkpoint(self):
        for exception_type, expected in [(FacebookError, 'failed'),
                                          (FacebookDeliveryUnknown, 'uncertain')]:
            with self.subTest(outcome=expected):
                # Every outcome uses an independent workspace ledger.
                self.city['workspace_key'] = expected
                destination = f'meta:123:{expected}'
                error = exception_type('The group feed request failed')
                error.stage = 'feed'
                error.uploaded_photo_ids = ['401', '402']
                self.client.publish_group.side_effect = error
                result = self.run_batch(max_groups=1)
                group = grouped.list_groups(self.conn, destination)[0]
                self.assertEqual(group['status'], expected)
                self.assertIn('stage=feed', group['error'])
                self.assertIn('known_uploaded_photo_ids=["401", "402"]', group['error'])
                self.assertIn('known_uploaded_photo_ids=["401", "402"]', result['errors'][0])
                self.assertFalse(result['complete'])

    def test_preparation_failure_creates_no_reservations_or_public_post(self):
        self.download.return_value = None
        before = self.state()
        result = self.run_batch()
        self.assertTrue(result['errors'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['remaining'], 23)
        self.assertEqual(self.state(), before)
        self.client.publish_group.assert_not_called()

    def test_incomplete_empty_or_failed_roster_never_publishes(self):
        before = self.state()
        for complete, rows, failure in [(False, self.rows, None), (True, [], None),
                                        (True, self.rows, RuntimeError('source failed'))]:
            with self.subTest(complete=complete, empty=not rows, failure=failure):
                self.adapter.snapshot_complete = complete
                self.adapter.snapshot_rows.return_value = rows
                self.adapter.snapshot_rows.side_effect = failure
                result = self.run_batch()
                self.assertTrue(result['errors'])
                self.assertFalse(result['complete'])
                self.assertEqual(self.state(), before)
        self.details.assert_not_called()
        self.client.publish_group.assert_not_called()

    def test_preview_counts_without_preparing_images_or_mutating_state(self):
        before = self.state()
        result = self.run_batch(max_groups=2, dry_run=True)
        self.assertEqual(result['groups_planned'], 2)
        self.assertEqual(result['people_published'], 0)
        self.assertEqual(result['remaining'], 23)
        self.assertEqual(self.state(), before)
        self.details.assert_not_called()
        self.download.assert_not_called()
        self.client.publish_group.assert_not_called()

    def test_wrong_photo_count_is_held_instead_of_counted_as_complete(self):
        self.client.publish_group.side_effect = None
        self.client.publish_group.return_value = {'post_id': '123_456', 'photo_ids': ['999']}
        result = self.run_batch()
        self.assertTrue(result['paused'])
        self.assertEqual(result['groups_published'], 0)
        self.assertEqual(result['people_published'], 0)
        self.assertEqual(len(delivery.list_unresolved(self.conn)), 10)
        self.assertEqual(grouped.list_groups(self.conn, self.destination)[0]['status'], 'uncertain')

    def test_remote_success_with_local_ack_failure_retains_ids_and_blocks_repeat(self):
        with patch.object(catchup.delivery, 'complete', side_effect=RuntimeError('disk failed')):
            result = self.run_batch()
        self.assertEqual(result['groups_published'], 1)
        self.assertEqual(result['people_published'], 10)
        self.assertTrue(result['paused'])
        self.assertTrue(result['errors'])
        group = grouped.list_groups(self.conn, self.destination)[0]
        self.assertEqual(group['status'], 'published')
        self.assertTrue(group['post_id'])
        self.assertEqual(len(group['photo_ids']), 10)
        self.assertTrue(self.run_batch()['paused'])
        self.client.publish_group.assert_called_once()

    def test_include_previous_archives_individual_receipt_and_replaces_tracking_once(self):
        first = self.identity(self.rows[0])
        source = catchup.source_key(self.city, self.city['sources'][0])
        delivery.claim(self.conn, first, self.destination)
        delivery.complete(self.conn, first, self.destination, 'meta:123_999')
        custody.track(self.conn, first, self.destination, source, '123_999',
                      'Original individual caption', self.rows[0]['detail_url'], self.rows[0]['name'])
        # A receipt for a person no longer in the current snapshot must be untouched.
        delivery.claim(self.conn, 'former-roster-member', self.destination)
        delivery.complete(self.conn, 'former-roster-member', self.destination, 'meta:123_888')
        result = self.run_batch(max_groups=3, include_previous=True)
        self.assertTrue(result['complete'])
        self.assertEqual(result['people_published'], 23)
        self.assertEqual(result['replaced_individuals'], 1)
        archive = self.conn.execute(
            'SELECT remote_ref,delivery_json,custody_json FROM catchup_replaced_deliveries'
        ).fetchone()
        self.assertEqual(archive[0], 'meta:123_999')
        self.assertEqual(json.loads(archive[1])['record_hash'], first)
        self.assertEqual(json.loads(archive[2])['original_caption'], 'Original individual caption')
        new_ref = self.conn.execute('SELECT remote_ref FROM deliveries WHERE record_hash=?', (first,)).fetchone()[0]
        self.assertNotEqual(new_ref, 'meta:123_999')
        tracked_ref = self.conn.execute('SELECT remote_ref FROM custody_posts WHERE record_hash=?', (first,)).fetchone()[0]
        self.assertEqual(new_ref, 'meta:' + tracked_ref)
        self.assertEqual(self.conn.execute('SELECT remote_ref FROM deliveries WHERE record_hash=?',
                                          ('former-roster-member',)).fetchone()[0], 'meta:123_888')
        self.client.delete.assert_not_called()
        before = self.state()
        calls = self.client.publish_group.call_count
        again = self.run_batch(max_groups=3, include_previous=True)
        self.assertTrue(again['complete'])
        self.assertEqual(again['replaced_individuals'], 0)
        self.assertEqual(self.client.publish_group.call_count, calls)
        self.assertEqual(self.state(), before)

    def test_include_previous_preview_does_not_create_archive_or_change_history(self):
        first = self.identity(self.rows[0])
        delivery.claim(self.conn, first, self.destination)
        delivery.complete(self.conn, first, self.destination, 'meta:123_999')
        before = self.state()
        normal = self.run_batch(dry_run=True)
        reset = self.run_batch(dry_run=True, include_previous=True)
        self.assertEqual(normal['remaining'], 22)
        self.assertEqual(reset['remaining'], 23)
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='catchup_replaced_deliveries'").fetchone())
        self.client.publish_group.assert_not_called()

    def test_include_previous_keeps_prior_published_group_members_out_of_later_batches(self):
        first = self.run_batch(max_groups=1, include_previous=True)
        self.assertEqual(first['people_published'], 10)
        first_members = {member['hash'] for member in grouped.list_groups(
            self.conn, self.destination, 'published')[0]['members']}
        before_custody = self.conn.execute('SELECT COUNT(*) FROM custody_posts').fetchone()[0]
        second = self.run_batch(max_groups=1, include_previous=True)
        self.assertEqual(second['people_published'], 10)
        self.assertEqual(second['replaced_individuals'], 0)
        all_groups = grouped.list_groups(self.conn, self.destination, 'published')
        self.assertEqual(len(all_groups), 2)
        second_members = next({member['hash'] for member in group['members']} for group in all_groups
                              if {member['hash'] for member in group['members']} != first_members)
        self.assertFalse(first_members & second_members)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM custody_posts').fetchone()[0],
                         before_custody + 10)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM catchup_replaced_deliveries').fetchone()[0], 0)

    def test_include_previous_archive_and_canonical_removal_are_atomic(self):
        first = self.identity(self.rows[0])
        source = catchup.source_key(self.city, self.city['sources'][0])
        delivery.claim(self.conn, first, self.destination)
        delivery.complete(self.conn, first, self.destination, 'meta:123_999')
        custody.track(self.conn, first, self.destination, source, '123_999',
                      'Original', self.rows[0]['detail_url'])
        self.conn.execute("CREATE TRIGGER reject_custody_removal BEFORE DELETE ON custody_posts "
                          "BEGIN SELECT RAISE(ABORT, 'simulated local failure'); END")
        self.conn.commit()
        before = self.state()
        result = self.run_batch(include_previous=True)
        self.assertTrue(result['paused'])
        self.assertTrue(result['errors'])
        self.assertEqual(self.state(), before)
        self.client.publish_group.assert_not_called()
        self.details.assert_not_called()


if __name__ == '__main__':
    unittest.main()
