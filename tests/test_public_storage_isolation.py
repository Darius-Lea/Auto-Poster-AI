"""Public recovery assets remain available while account storage is blocked."""
import importlib
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class PublicStorageIsolationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.folder.name + '/accounts.db',
            'AUTOPOST_WEB_SECRET': 'synthetic-public-storage-isolation-secret-over-32',
            'AUTOPOST_APP_PASSWORD': '', 'AUTOPOST_PREVIEW_MODE': 'true',
            'AUTOPOST_CLIP_ENABLED': 'false',
        })
        environment.start()
        self.addCleanup(environment.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.app.config['TESTING'] = True

    def signed_client(self):
        client = self.web.app.test_client()
        with client.session_transaction() as session:
            session.update(authorized=True, account_id='existing-customer', csrf='synthetic')
        return client

    def test_signed_public_pages_assets_and_health_need_no_account_storage(self):
        client = self.signed_client()
        with patch.object(self.web, 'ensure_database_ready', side_effect=AssertionError('bootstrap attempted')), \
             patch.object(self.web.customer_accounts, 'get', side_effect=AssertionError('account lookup attempted')):
            for path in ('/version', '/welcome', '/start', '/offline', '/service-worker.js',
                         '/static/clip-studio.js', '/static/tool-sidebar.css'):
                with self.subTest(path=path):
                    with client.get(path) as response:
                        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.environ['AUTOPOST_WEB_DB']))

    def test_waiting_for_other_bootstrap_returns_retryable_response_instead_of_hanging(self):
        held = threading.Event()
        release = threading.Event()
        def hold_bootstrap():
            with self.web._BOOTSTRAP_LOCK:
                held.set()
                release.wait(10)
        thread = threading.Thread(target=hold_bootstrap)
        thread.start()
        self.assertTrue(held.wait(1))
        try:
            client = self.web.app.test_client()
            client.get('/account?mode=login')
            with client.session_transaction() as session:
                token = session['csrf']
            started = time.monotonic()
            response = client.post('/account', data={
                '_csrf':token, 'mode':'login', 'email':'test@example.test',
                'password':'synthetic-password123',
            })
            self.assertEqual(response.status_code, 503)
            self.assertLess(time.monotonic() - started, 3.5)
            self.assertEqual(response.headers['Retry-After'], '2')
            self.assertEqual(self.signed_client().get('/version').status_code, 200)
        finally:
            release.set()
            thread.join(2)
