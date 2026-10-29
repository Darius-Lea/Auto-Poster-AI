import unittest
from unittest.mock import Mock, patch

from adapters.allen_sheriff import AllenSheriffAdapter


class RosterSnapshotTests(unittest.TestCase):
    def row(self, identifier, *, date='01/02/1999', first='Synthetic', image='photo.jpg'):
        return (
            '<tr><td><img src="' + image + '"></td><td>' + first + '</td>'
            '<td>Fixture</td><td>' + date + '</td><td><a href="/detail/'
            + identifier + '">Details</a></td></tr>'
        )

    def page(self, *rows):
        return '<table><tbody>' + ''.join(rows) + '</tbody></table>'

    def test_snapshot_retains_old_seen_records_without_detail_requests_or_cap(self):
        adapter = AllenSheriffAdapter()
        adapter.max_per_run = 1
        adapter.seen_check = Mock(return_value=True)
        pages = [self.page(self.row('old'), self.row('new', date='10/28/2026'))]
        with patch.object(adapter, '_load_list_html', return_value=pages), \
                patch('adapters.allen_sheriff.requests.get') as get, \
                patch('adapters.allen_sheriff.MAX_AGE_DAYS', 3):
            rows = adapter.snapshot_rows('https://source.example/roster')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['booking_date'], '01/02/1999')
        adapter.seen_check.assert_not_called()
        get.assert_not_called()
        self.assertTrue(adapter.snapshot_complete)
        self.assertEqual(adapter.last_snapshot, rows)

    def test_snapshot_deduplicates_across_pages(self):
        adapter = AllenSheriffAdapter()
        pages = [self.page(self.row('one')), self.page(self.row('one'), self.row('two'))]
        with patch.object(adapter, '_load_list_html', return_value=pages):
            rows = adapter.snapshot_rows('https://source.example/roster')
        self.assertEqual([r['detail_url'] for r in rows],
                         ['https://source.example/detail/one', 'https://source.example/detail/two'])

    def test_malformed_candidate_row_invalidates_entire_snapshot(self):
        for bad in [self.row('bad', date='not a date'), self.row('bad', first=''),
                    self.row('bad', image=''),
                    '<tr><td><a href="/detail/bad">Missing fields</a></td></tr>']:
            with self.subTest(bad=bad):
                adapter = AllenSheriffAdapter()
                with patch.object(adapter, '_load_list_html',
                                  return_value=[self.page(self.row('good'), bad)]), \
                        self.assertRaisesRegex(RuntimeError, 'snapshot is incomplete'):
                    adapter.snapshot_rows('https://source.example/roster')
                self.assertFalse(adapter.snapshot_complete)
                self.assertIsNone(adapter.last_snapshot)

    def test_declared_count_detects_missing_pages(self):
        for attribute in ['class="dataTables_info"', 'class="dt-info"', 'id="roster_info"']:
            with self.subTest(attribute=attribute):
                adapter = AllenSheriffAdapter()
                html = self.page(self.row('one'), self.row('two')) + (
                    '<div ' + attribute + '>Showing 1 to 2 of 780 entries</div>'
                )
                with patch.object(adapter, '_load_list_html', return_value=[html]), \
                        self.assertRaisesRegex(RuntimeError, 'declares 780.*only 2.*incomplete'):
                    adapter.snapshot_rows('https://source.example/roster')
                self.assertFalse(adapter.snapshot_complete)
                self.assertIsNone(adapter.last_snapshot)

    def test_filtered_count_uses_active_count_not_original_total(self):
        adapter = AllenSheriffAdapter()
        html = self.page(self.row('one'), self.row('two')) + (
            '<div class="dt-info">Showing 1 to 2 of 2 entries '
            '(filtered from 1,234 total entries)</div>'
        )
        with patch.object(adapter, '_load_list_html', return_value=[html]):
            rows = adapter.snapshot_rows('https://source.example/roster')
        self.assertEqual(len(rows), 2)
        self.assertTrue(adapter.snapshot_complete)

    def test_repeated_page_info_does_not_add_declared_counts(self):
        adapter = AllenSheriffAdapter()
        pages = [
            self.page(self.row('one')) + '<div class="dt-info">Showing 1 to 1 of 2 entries</div>',
            self.page(self.row('two')) + '<div class="dt-info">Showing 2 to 2 of 2 entries</div>',
        ]
        with patch.object(adapter, '_load_list_html', return_value=pages):
            self.assertEqual(len(adapter.snapshot_rows('https://source.example/roster')), 2)

    def test_genuine_empty_roster_is_returned_for_monitor_policy(self):
        adapter = AllenSheriffAdapter()
        with patch.object(adapter, '_load_list_html', return_value=[self.page()]):
            self.assertEqual(adapter.snapshot_rows('https://source.example/roster'), [])
        self.assertTrue(adapter.snapshot_complete)
        self.assertEqual(adapter.last_snapshot, [])

    def test_failed_snapshot_clears_previous_complete_snapshot(self):
        adapter = AllenSheriffAdapter()
        with patch.object(adapter, '_load_list_html', return_value=[self.page(self.row('one'))]):
            adapter.snapshot_rows('https://source.example/roster')
        with patch.object(adapter, '_load_list_html', side_effect=RuntimeError('pagination failed')), \
                self.assertRaisesRegex(RuntimeError, 'Could not load Allen Sheriff roster'):
            adapter.snapshot_rows('https://source.example/roster')
        self.assertFalse(adapter.snapshot_complete)
        self.assertIsNone(adapter.last_snapshot)

    def test_fetch_exposes_full_snapshot_before_seen_and_per_run_filters(self):
        adapter = AllenSheriffAdapter()
        adapter.max_per_run = 1
        adapter.seen_check = lambda name, *_: name.startswith('Seen')
        html = self.page(self.row('seen', first='Seen'), self.row('unseen'),
                         self.row('later', date='10/28/2026'))
        response = Mock(text='<p>Age: 30</p><p>Statute Description: Example</p>')
        with patch.object(adapter, '_load_list_html', return_value=[html]), \
                patch('adapters.allen_sheriff.requests.get', return_value=response), \
                patch('adapters.allen_sheriff.time.sleep'), \
                patch('adapters.allen_sheriff.MAX_AGE_DAYS', 0):
            records = adapter.fetch('https://source.example/roster')
        self.assertEqual(len(records), 1)
        self.assertEqual(len(adapter.last_snapshot), 3)
        self.assertTrue(adapter.snapshot_complete)
