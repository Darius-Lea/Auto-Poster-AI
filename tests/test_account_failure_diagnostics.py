"""Customer account failures show accurate, safe recovery information."""
import importlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class AccountFailureDiagnosticsTests(unittest.TestCase):
    password = 'synthetic-diagnostic-account-password123'
    email = 'customer@example.test'

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / 'accounts.db'
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': str(self.database),
            'AUTOPOST_CLIP_DATA_DIR': str(Path(self.folder.name) / 'clips'),
            'AUTOPOST_WEB_SECRET': 'synthetic-account-diagnostic-secret-at-least-32',
            'AUTOPOST_APP_PASSWORD': '',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
            'AUTOPOST_PREVIEW_MODE': 'true',
            'AUTOPOST_CLIP_ENABLED': 'false',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.load_app()

    def load_app(self):
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.app.config['TESTING'] = True

    def form(self, mode='register'):
        client = self.web.app.test_client()
        self.assertEqual(client.get('/account?mode=' + mode).status_code, 200)
        with client.session_transaction() as session:
            token = session['csrf']
        return client, {
            '_csrf': token, 'mode': mode, 'name': 'Business Owner',
            'email': self.email, 'password': self.password,
        }

    def assert_safe_failure(self, response, code, *, retryable=False):
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.mimetype, 'text/html')
        self.assertEqual(response.headers['X-AutoPoster-Error'], code)
        reference = response.headers['X-Request-ID']
        self.assertTrue(re.fullmatch(r'[0-9a-f]{8}', reference))
        text = response.get_data(as_text=True)
        self.assertIn(code, text)
        self.assertIn(reference, text)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn(self.folder.name, text)
        self.assertNotIn(self.password, text)
        self.assertNotIn('Traceback', text)
        self.assertNotIn('OperationalError', text)
        if retryable:
            self.assertEqual(response.headers['Retry-After'], '2')
            self.assertIn('service is busy', text)
        else:
            self.assertNotIn('Retry-After', response.headers)
            self.assertNotIn('service is busy', text)

    def test_missing_database_parent_is_configuration_failure_without_retry_claim(self):
        self.database = Path(self.folder.name) / 'private-missing-parent' / 'accounts.db'
        with patch.dict(os.environ, {'AUTOPOST_WEB_DB': str(self.database)}):
            self.load_app()
            client, data = self.form('login')
            with self.assertLogs(self.web.app.logger, level='ERROR'):
                response = client.post('/account', data=data)
            self.assert_safe_failure(response, 'storage_configuration')
            self.assertFalse(self.database.exists())

    def test_password_memory_failure_does_not_create_account_or_claim_database_busy(self):
        self.web.init_db()
        client, data = self.form()
        with patch.object(self.web.customer_accounts, 'generate_password_hash',
                          side_effect=MemoryError('private cryptographic detail')), \
             self.assertLogs(self.web.app.logger, level='ERROR'):
            response = client.post('/account', data=data)
        self.assert_safe_failure(response, 'account_resources')
        self.assertNotIn(b'private cryptographic detail', response.data)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 0)
        finally:
            connection.close()

    def test_real_write_contention_is_retryable_and_recovers_after_unlock(self):
        self.web.init_db()
        client, data = self.form()
        held = sqlite3.connect(self.database)
        original_connect = self.web.customer_accounts.connect
        def short_connection():
            connection = original_connect()
            connection.execute('PRAGMA busy_timeout=1')
            return connection
        try:
            held.execute('BEGIN IMMEDIATE')
            with patch.object(self.web.customer_accounts, 'connect', side_effect=short_connection), \
                 self.assertLogs(self.web.app.logger, level='ERROR'):
                response = client.post('/account', data=data)
            self.assert_safe_failure(response, 'storage_busy', retryable=True)
        finally:
            held.close()
        response = client.post('/account', data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/').status_code, 200)

    def test_duplicate_registration_does_not_masquerade_as_service_failure(self):
        client, data = self.form()
        self.assertEqual(client.post('/account', data=data).status_code, 302)
        duplicate_client, duplicate_data = self.form()
        response = duplicate_client.post('/account', data=duplicate_data)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('X-AutoPoster-Error', response.headers)
        self.assertIn(b'Try signing in instead', response.data)
        login_client, login_data = self.form('login')
        self.assertEqual(login_client.post('/account', data=login_data).status_code, 302)
        self.assertEqual(login_client.get('/').status_code, 200)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 1)
        finally:
            connection.close()

    def test_corrupt_storage_shows_safe_diagnostic_instead_of_raw_database_error(self):
        self.database.write_bytes(b'private non-database data' * 40)
        client, data = self.form('login')
        with self.assertLogs(self.web.app.logger, level='ERROR'):
            response = client.post('/account', data=data)
        self.assert_safe_failure(response, 'storage_corrupt')
        self.assertNotIn(b'file is not a database', response.data)
        self.assertNotIn(b'private non-database data', response.data)
