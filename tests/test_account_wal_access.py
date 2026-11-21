"""Production initialization permits account access alongside held DB readers."""
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class AccountWalAccessTests(unittest.TestCase):
    def test_signup_logout_and_login_work_while_an_existing_reader_stays_open(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'accounts.db'
            environment = {
                'AUTOPOST_WEB_DB': str(database),
                'AUTOPOST_CLIP_DATA_DIR': str(Path(directory) / 'clips'),
                'AUTOPOST_WEB_SECRET': 'synthetic-wal-access-session-secret-longer-than32',
                'AUTOPOST_APP_PASSWORD': '', 'AUTOPOST_PREVIEW_MODE': 'true',
                'AUTOPOST_CLIP_ENABLED': 'false', 'AUTOPOST_REQUIRE_PAYMENT': 'false',
                'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
            }
            with patch.dict(os.environ, environment):
                import app_mvp
                web = importlib.reload(app_mvp)
                web.init_db()
                web.app.config['TESTING'] = True
                reader = sqlite3.connect(database)
                try:
                    self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                    reader.execute('BEGIN')
                    self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 0)
                    client = web.app.test_client()
                    self.assertEqual(client.get('/account?mode=register').status_code, 200)
                    with client.session_transaction() as session:
                        token = session['csrf']
                    credentials = {
                        'email': 'reader-concurrency@example.test',
                        'password': 'synthetic-concurrent-account-password123',
                    }
                    response = client.post('/account', data={
                        '_csrf': token, 'mode': 'register', 'name': 'Business Owner',
                        **credentials,
                    }, follow_redirects=True)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'Prepare your next post', response.data)
                    with client.session_transaction() as session:
                        original_id = session['account_id']
                    self.assertEqual(client.get('/logout').status_code, 302)
                    self.assertEqual(client.get('/account?mode=login').status_code, 200)
                    with client.session_transaction() as session:
                        token = session['csrf']
                    response = client.post('/account', data={
                        '_csrf': token, 'mode': 'login', **credentials,
                    }, follow_redirects=True)
                    self.assertEqual(response.status_code, 200)
                    with client.session_transaction() as session:
                        self.assertEqual(session['account_id'], original_id)
                    # Keep the same old read snapshot throughout the entire flow.
                    self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 0)
                    reader.rollback()
                    self.assertEqual(reader.execute('SELECT COUNT(*) FROM customer_accounts').fetchone()[0], 1)
                finally:
                    reader.close()
