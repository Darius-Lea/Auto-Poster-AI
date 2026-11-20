"""Real post-login contention must not leave the account form waiting 15 seconds."""
import builtins
import importlib
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch


class DashboardStorageLatencyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.folder.name + '/accounts.db',
            'AUTOPOST_CLIP_DATA_DIR': self.folder.name + '/clips',
            'AUTOPOST_WEB_SECRET': 'synthetic-dashboard-latency-secret-at-least-32',
            'AUTOPOST_APP_PASSWORD': '', 'AUTOPOST_PREVIEW_MODE': 'true',
            'AUTOPOST_CLIP_ENABLED': 'false', 'AUTOPOST_REQUIRE_PAYMENT': 'false',
        })
        environment.start()
        self.addCleanup(environment.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.init_db()
        self.web.app.config['TESTING'] = True

    def test_all_dashboard_storage_waits_are_short_only_inside_requests(self):
        import app_control, meta_accounts, automation_setup, public_setup
        factories = (app_control.connect, meta_accounts._db,
                     automation_setup.connect, public_setup.connect)
        for factory in factories:
            with self.subTest(factory=factory.__module__):
                connection = factory()
                try:
                    self.assertEqual(connection.execute('PRAGMA busy_timeout').fetchone()[0], 15000)
                finally:
                    connection.close()
                with self.web.app.test_request_context('/'):
                    connection = factory()
                    try:
                        self.assertEqual(connection.execute('PRAGMA busy_timeout').fetchone()[0], 2000)
                    finally:
                        connection.close()

    def test_real_writer_after_login_does_not_block_wal_dashboard_reads(self):
        import customer_accounts, meta_accounts
        password = 'synthetic-dashboard-password123'
        user = customer_accounts.register('dashboard@example.test', password, 'Dashboard')
        client = self.web.app.test_client()
        client.get('/account?mode=login')
        with client.session_transaction() as session:
            csrf = session['csrf']
        holder = []
        original_connect = meta_accounts._db
        def lock_after_dashboard_reads():
            connection = original_connect()
            writer = sqlite3.connect(os.environ['AUTOPOST_WEB_DB'], timeout=2)
            writer.execute('BEGIN EXCLUSIVE')
            holder.append(writer)
            return connection
        try:
            with patch.object(meta_accounts, '_db', lock_after_dashboard_reads):
                started = time.monotonic()
                response = client.post('/account', data={
                    '_csrf':csrf, 'mode':'login', 'email':'dashboard@example.test',
                    'password':password,
                }, follow_redirects=True)
                elapsed = time.monotonic() - started
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('X-AutoPoster-Error', response.headers)
            self.assertLess(elapsed, 3.5)
            with client.session_transaction() as session:
                self.assertEqual(session['account_id'], user['id'])
                self.assertIs(session['authorized'], True)
        finally:
            for writer in holder:
                writer.rollback()
                writer.close()
        self.assertEqual(client.get('/').status_code, 200)
        with sqlite3.connect(os.environ['AUTOPOST_WEB_DB']) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 1)
            self.assertTrue(connection.execute('SELECT password_hash FROM customer_accounts').fetchone()[0].startswith('scrypt:'))

    def test_legacy_worker_helper_does_not_require_flask(self):
        from sqlite_runtime import busy_timeout_ms
        original_import = builtins.__import__
        def without_flask(name, *args, **kwargs):
            if name == 'flask':
                raise ImportError('Flask absent from the original publisher dependencies')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_flask):
            self.assertEqual(busy_timeout_ms(), 15000)
