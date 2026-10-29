import builtins
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core import catchup_control, catchup_runner


class CatchupRunnerInspectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'catchup.db'
        self.output = Path(self.folder.name) / 'output'
        self.destination = 'meta:123:fort-wayne'
        self.session = '99:2'

    def create_state(self, status='active', session=None):
        with sqlite3.connect(self.path) as conn:
            catchup_control.initialize(conn)
            catchup_control.begin(conn, self.destination, session or self.session)
            if status == 'completed':
                catchup_control.finish_batch(conn, self.destination, session or self.session,
                                              {'complete': True, 'errors': [], 'remaining': 0})
            elif status == 'paused':
                catchup_control.finish_batch(conn, self.destination, session or self.session,
                                              {'complete': False, 'paused': True, 'errors': ['Check prior batch']})

    def snapshot(self):
        with sqlite3.connect(self.path) as conn:
            return tuple(conn.iterdump())

    def run_cli(self, argument):
        environment = {'AUTOPOST_DB': str(self.path), 'META_PAGE_ID': '123',
                       'CATCHUP_WORKSPACE': 'fort-wayne', 'GITHUB_RUN_ID': '99',
                       'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_OUTPUT': str(self.output)}
        original_import = builtins.__import__

        def no_application_import(name, *args, **kwargs):
            if name == 'main' or name in ('core.catchup', 'core.facebook'):
                self.fail('Inspect/completion checks must not import the application or HTTP clients')
            return original_import(name, *args, **kwargs)

        with patch.dict(os.environ, environment, clear=True), \
                patch('sys.argv', ['catchup_runner.py', argument]), \
                patch('builtins.__import__', side_effect=no_application_import), \
                patch('builtins.print'):
            return catchup_runner.main()

    def test_missing_database_requires_work_without_creating_a_database(self):
        self.assertTrue(catchup_runner.should_run(str(self.path), self.destination, self.session))
        self.assertFalse(self.path.exists())

    def test_legacy_database_without_control_table_is_not_mutated(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE retained_deliveries (reference TEXT)')
            conn.execute("INSERT INTO retained_deliveries VALUES ('123_456')")
        before = self.snapshot()
        self.assertTrue(catchup_runner.should_run(str(self.path), self.destination, self.session))
        self.assertEqual(self.snapshot(), before)

    def test_same_active_session_continues_without_mutating_state(self):
        self.create_state()
        before = self.snapshot()
        self.assertTrue(catchup_runner.should_run(str(self.path), self.destination, self.session))
        self.assertEqual(self.snapshot(), before)

    def test_same_completed_session_skips_but_different_session_starts(self):
        self.create_state('completed')
        before = self.snapshot()
        self.assertFalse(catchup_runner.should_run(str(self.path), self.destination, self.session))
        self.assertTrue(catchup_runner.should_run(str(self.path), self.destination, '100:1'))
        self.assertEqual(self.snapshot(), before)

    def test_same_paused_session_fails_without_mutating_state(self):
        self.create_state('paused')
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'paused'):
            catchup_runner.should_run(str(self.path), self.destination, self.session)
        self.assertEqual(self.snapshot(), before)

    def test_inspect_cli_writes_run_output_without_application_imports_or_database_creation(self):
        self.assertEqual(self.run_cli('--inspect-only'), 0)
        self.assertEqual(self.output.read_text(), 'run=true\n')
        self.assertFalse(self.path.exists())

    def test_completed_inspect_cli_writes_false_without_mutation(self):
        self.create_state('completed')
        before = self.snapshot()
        self.assertEqual(self.run_cli('--inspect-only'), 0)
        self.assertEqual(self.output.read_text(), 'run=false\n')
        self.assertEqual(self.snapshot(), before)

    def test_verify_complete_requires_this_sessions_completed_state(self):
        with self.assertRaisesRegex(RuntimeError, 'not fully covered'):
            self.run_cli('--verify-complete')
        self.assertFalse(self.path.exists())
        self.create_state('completed', session='previous:1')
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'not fully covered'):
            self.run_cli('--verify-complete')
        self.assertEqual(self.snapshot(), before)
        self.create_state('completed')
        before = self.snapshot()
        self.assertEqual(self.run_cli('--verify-complete'), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output.exists())

    def test_verify_paused_session_cannot_claim_completion(self):
        self.create_state('paused')
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'paused'):
            self.run_cli('--verify-complete')
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
