import sqlite3
import unittest

from core import catchup_control


class CatchupControlTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        catchup_control.initialize(self.conn)
        self.destination = 'meta:123:fort-wayne'

    def test_new_catchup_blocks_normal_until_it_is_completed(self):
        self.assertIsNone(catchup_control.get(self.conn, self.destination))
        self.assertFalse(catchup_control.blocks_normal(self.conn, self.destination))
        state = catchup_control.begin(self.conn, self.destination, 'run-1')
        self.assertEqual(state['status'], 'active')
        self.assertTrue(catchup_control.blocks_normal(self.conn, self.destination))
        report = {'groups_published': 1, 'people_published': 10, 'remaining': 9,
                  'complete': False, 'errors': [], 'paused': False}
        catchup_control.finish_batch(self.conn, self.destination, 'run-1', report)
        self.assertTrue(catchup_control.blocks_normal(self.conn, self.destination))
        completed = catchup_control.finish_batch(self.conn, self.destination, 'run-1',
                                                {**report, 'remaining': 0, 'complete': True})
        self.assertEqual(completed['status'], 'completed')
        self.assertFalse(catchup_control.blocks_normal(self.conn, self.destination))

    def test_same_session_begin_retains_outcome_and_updated_time(self):
        catchup_control.begin(self.conn, self.destination, 'run-1')
        stored = catchup_control.finish_batch(self.conn, self.destination, 'run-1',
                                              {'complete': True, 'errors': [], 'remaining': 0})
        self.assertEqual(catchup_control.begin(self.conn, self.destination, 'run-1'), stored)

    def test_errors_or_uncertainty_pause_even_if_report_claims_complete(self):
        for report in [{'errors': ['Lost upload response'], 'complete': True},
                       {'errors': [], 'paused': True, 'complete': True}]:
            with self.subTest(report=report):
                catchup_control.begin(self.conn, self.destination, 'run-1')
                state = catchup_control.finish_batch(self.conn, self.destination, 'run-1', report)
                self.assertEqual(state['status'], 'paused')
                self.assertTrue(catchup_control.blocks_normal(self.conn, self.destination))

    def test_new_authorized_session_resumes_without_touching_delivery_history(self):
        self.conn.execute('CREATE TABLE existing_deliveries (reference TEXT)')
        self.conn.execute("INSERT INTO existing_deliveries VALUES ('123_456')")
        self.conn.commit()
        catchup_control.begin(self.conn, self.destination, 'run-1')
        catchup_control.finish_batch(self.conn, self.destination, 'run-1', {'paused': True})
        state = catchup_control.begin(self.conn, self.destination, 'run-2')
        self.assertEqual(state['session_id'], 'run-2')
        self.assertEqual(state['status'], 'active')
        self.assertEqual(state['report'], {})
        self.assertEqual(self.conn.execute('SELECT reference FROM existing_deliveries').fetchone(), ('123_456',))

    def test_destinations_do_not_block_each_other(self):
        catchup_control.begin(self.conn, self.destination, 'run-1')
        self.assertFalse(catchup_control.blocks_normal(self.conn, 'meta:999:other'))

    def test_old_or_missing_session_cannot_overwrite_current_progress(self):
        before = catchup_control.begin(self.conn, self.destination, 'run-2')
        with self.assertRaisesRegex(ValueError, 'session does not match'):
            catchup_control.finish_batch(self.conn, self.destination, 'run-1', {'complete': True})
        self.assertEqual(catchup_control.get(self.conn, self.destination), before)
        with self.assertRaises(ValueError):
            catchup_control.finish_batch(self.conn, 'meta:999:other', 'run-1', {'complete': True})
        self.assertIsNone(catchup_control.get(self.conn, 'meta:999:other'))

    def test_report_is_saved_as_an_independent_snapshot(self):
        catchup_control.begin(self.conn, self.destination, 'run-1')
        report = {'remaining': 7, 'errors': [], 'complete': False}
        catchup_control.finish_batch(self.conn, self.destination, 'run-1', report)
        report['errors'].append('caller mutation')
        self.assertEqual(catchup_control.get(self.conn, self.destination)['report']['errors'], [])

    def test_invalid_inputs_leave_saved_progress_unchanged(self):
        before = catchup_control.begin(self.conn, self.destination, 'run-1')
        for report in [None, [], {'complete': 'false'}, {'paused': 'false'}, {'remaining': float('nan')}]:
            with self.subTest(report=report), self.assertRaises((ValueError, TypeError)):
                catchup_control.finish_batch(self.conn, self.destination, 'run-1', report)
        self.assertEqual(catchup_control.get(self.conn, self.destination), before)
        with self.assertRaises(ValueError):
            catchup_control.begin(self.conn, '', 'run-1')
        with self.assertRaises(ValueError):
            catchup_control.begin(self.conn, self.destination, '')


if __name__ == '__main__':
    unittest.main()
