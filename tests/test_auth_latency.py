"""The public sign-in screens must not wait for databases, models or providers."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class AuthEntryDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/app.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/clips',
            'AUTOPOST_WEB_SECRET': 'synthetic-auth-entry-secret-more-than32',
            'AUTOPOST_APP_PASSWORD': '',
            'AUTOPOST_CLIP_ENABLED': 'false',
            'AUTOPOST_PREVIEW_MODE': 'true',
        })
        env.start()
        self.addCleanup(env.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.init_db()
        self.web.app.config['TESTING'] = True

    def test_public_account_screens_do_not_load_customer_storage_or_models(self):
        client = self.web.app.test_client()
        unavailable = AssertionError('A public authentication screen invoked a blocking dependency')
        with patch('customer_accounts.connect', side_effect=unavailable), \
                patch('customer_accounts.generate_password_hash', side_effect=unavailable), \
                patch('customer_accounts.check_password_hash', side_effect=unavailable), \
                patch('clip_studio_web.renderer_readiness', side_effect=unavailable), \
                patch('requests.sessions.Session.request', side_effect=unavailable):
            sign_in = client.get('/account?mode=login&next=clips')
            self.assertEqual(sign_in.status_code, 200)
            self.assertIn(b'Sign in and continue', sign_in.data)
            sign_up = client.get('/account?mode=register&next=clips')
            self.assertEqual(sign_up.status_code, 200)
            self.assertIn(b'Create account and continue', sign_up.data)
            with client.session_transaction() as session:
                self.assertTrue(session.get('csrf'))
                self.assertIsNot(session.get('authorized'), True)

    def test_old_login_bookmark_still_offers_customers_entry_without_owner_setup(self):
        client = self.web.app.test_client()
        with patch('customer_accounts.connect', side_effect=AssertionError('Customer entry should not query storage')), \
                patch('requests.sessions.Session.request', side_effect=AssertionError('Customer entry should not call providers')):
            response = client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/account?mode=login"', response.data)
        self.assertIn(b'href="/account?mode=register"', response.data)
        self.assertNotIn(b'placeholder="App password"', response.data)


if __name__ == '__main__':
    unittest.main()
