"""Customer entry must initialize a restored account-only database before use.

These checks deliberately do not call app_mvp.init_db(): that was the missing
condition hidden by earlier client tests. No provider, mail or model is used.
"""
from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class PostLoginRecoveryTests(unittest.TestCase):
    password = 'synthetic-postlogin-password123'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/restored-accounts.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/private-clips',
            'AUTOPOST_WEB_SECRET': 'synthetic-postlogin-session-secret-at-least-32',
            'AUTOPOST_APP_PASSWORD': '',
            'AUTOPOST_REQUIRE_PAYMENT': 'true',
            'AUTOPOST_PREVIEW_MODE': 'false',
            'AUTOPOST_CLIP_ENABLED': 'false',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
            'META_APP_ID': '', 'META_APP_SECRET': '',
            'META_PAGE_ID': '', 'META_PAGE_ACCESS_TOKEN': '',
            'CANVA_CLIENT_ID': '', 'CANVA_CLIENT_SECRET': '',
            'STRIPE_SECRET_KEY': '',
        })
        environment.start()
        self.addCleanup(environment.stop)
        import customer_accounts
        self.accounts = customer_accounts
        # A persisted customer table can exist even if the deployed entry point
        # never initialized dashboard, Page, draft or subscription tables.
        self.accounts.init()

    def web(self):
        import app_mvp
        web = importlib.reload(app_mvp)
        web.app.config['TESTING'] = True
        return web

    def token(self, client):
        with client.session_transaction() as session:
            return session['csrf']

    def test_registration_opens_workspace_with_restored_partial_schema(self):
        web = self.web()
        client = web.app.test_client()
        self.assertEqual(client.get('/account?mode=register').status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'register',
            'name': 'New Business Owner', 'email': 'new@example.test',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')
        dashboard = client.get(response.location)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'Prepare your next post', dashboard.data)
        self.assertEqual(client.get('/clips').status_code, 200)

    def test_existing_customer_login_restores_workspace_without_provider_keys(self):
        original = self.accounts.register('returning@example.test', self.password,
                                          'Returning Business Owner')
        web = self.web()
        client = web.app.test_client()
        self.assertEqual(client.get('/account?mode=login').status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.token(client), 'mode': 'login',
            'email': 'RETURNING@example.test', 'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session['account_id'], original['id'])
        for path in ('/', '/clips', '/studio', '/control', '/account/settings'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(self.accounts.get(original['id'])['workspace_id'],
                         original['workspace_id'])


if __name__ == '__main__':
    unittest.main()
