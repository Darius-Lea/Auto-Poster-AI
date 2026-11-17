"""Customer sign-in works through direct WSGI imports, without a manual init call."""
import importlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from werkzeug.security import generate_password_hash


class AuthBootstrapTests(unittest.TestCase):
    password = 'synthetic-bootstrap-password123'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/accounts.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/private-clips',
            'AUTOPOST_APP_PASSWORD': '',
            'AUTOPOST_WEB_SECRET': 'synthetic-bootstrap-session-secret-at-least-32',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
            'AUTOPOST_REQUIRE_PAYMENT': 'false',
            'AUTOPOST_PREVIEW_MODE': 'true',
            'AUTOPOST_CLIP_ENABLED': 'false',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def load_direct_app(self):
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.app.config['TESTING'] = True
        # Deliberately do not call init_db(): this is gunicorn app_mvp:app.

    @staticmethod
    def token(client):
        with client.session_transaction() as session:
            return session['csrf']

    def register(self, email):
        client = self.web.app.test_client()
        self.assertEqual(client.get('/account?mode=register').status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'register', 'name': 'New Customer',
            'email': email, 'password': self.password,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            self.assertTrue(session['account_id'])
        return client

    def test_fresh_direct_wsgi_registration_logout_and_login_reach_workspace(self):
        self.load_direct_app()
        client = self.register('new@example.test')
        client.get('/logout')
        self.assertEqual(client.get('/account?mode=login').status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'login',
            'email': 'new@example.test', 'password': self.password,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Prepare your next post', response.data)
        with sqlite3.connect(os.environ['AUTOPOST_WEB_DB']) as connection:
            rows = connection.execute('SELECT password_hash FROM customer_accounts').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], self.password)

    def test_signin_to_fresh_storage_returns_friendly_invalid_credentials(self):
        self.load_direct_app()
        client = self.web.app.test_client()
        self.assertEqual(client.get('/account?mode=login').status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'login',
            'email': 'unknown@example.test', 'password': self.password,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Check your email and password', response.data)

    def test_direct_wsgi_migrates_an_existing_customer_and_preserves_owner_posts(self):
        digest = generate_password_hash(self.password, method='scrypt')
        with sqlite3.connect(os.environ['AUTOPOST_WEB_DB']) as connection:
            connection.executescript('''
                CREATE TABLE customer_accounts(
                  id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL UNIQUE,
                  email TEXT NOT NULL UNIQUE,name TEXT NOT NULL,
                  password_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE app_posts(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,
                  platform TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL,
                  remote_id TEXT,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            connection.execute('INSERT INTO customer_accounts VALUES(?,?,?,?,?,?)',
                ('legacy-user', 'ws_legacy', 'returning@example.test', 'Returning Customer', digest, 1))
            connection.execute("INSERT INTO app_posts(content,platform,due_at,status) VALUES('Private owner record','draft','2026-11-01T12:00:00+00:00','draft_ready')")
        self.load_direct_app()
        client = self.web.app.test_client()
        client.get('/account?mode=login')
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'login',
            'email': 'returning@example.test', 'password': self.password,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Private owner record', response.data)
        with sqlite3.connect(os.environ['AUTOPOST_WEB_DB']) as connection:
            saved = connection.execute('SELECT password_hash,auth_version,email_verified FROM customer_accounts').fetchone()
            owner_post = connection.execute('SELECT content,workspace_id FROM app_posts').fetchone()
        self.assertEqual(saved, (digest, 0, 0))
        self.assertEqual(owner_post, ('Private owner record', 'owner'))

    def test_two_first_registrations_bootstrap_storage_without_migration_races(self):
        self.load_direct_app()
        with ThreadPoolExecutor(max_workers=2) as pool:
            clients = list(pool.map(self.register, ('one@example.test', 'two@example.test')))
        with sqlite3.connect(os.environ['AUTOPOST_WEB_DB']) as connection:
            identities = connection.execute('SELECT id,workspace_id FROM customer_accounts').fetchall()
        self.assertEqual(len(identities), 2)
        self.assertNotEqual(identities[0][1], identities[1][1])
        self.assertTrue(all(client.get('/').status_code == 200 for client in clients))


if __name__ == '__main__':
    unittest.main()
