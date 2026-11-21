"""One customer identity opens posting, Clip Studio and persistent branding."""
import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


class SharedAccountJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/accounts.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/private-clips',
            'AUTOPOST_APP_PASSWORD': '',
            'AUTOPOST_WEB_SECRET': 'synthetic-shared-account-session-secret-123',
            'AUTOPOST_REQUIRE_PAYMENT': 'false',
            'AUTOPOST_PREVIEW_MODE': 'true',
            'AUTOPOST_CLIP_ENABLED': 'false',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED': 'true',
        })
        environment.start()
        self.addCleanup(environment.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.init_db()
        self.web.app.config['TESTING'] = True
        self.password = 'synthetic-shared-customer-password123'

    @staticmethod
    def csrf(client):
        with client.session_transaction() as session:
            return session['csrf']

    def register(self, email='new@example.test'):
        client = self.web.app.test_client()
        page = client.get('/account?mode=register&next=clips')
        self.assertEqual(page.status_code, 200)
        response = client.post('/account', data={
            '_csrf': self.csrf(client), 'mode': 'register',
            'name': 'New Business Owner', 'email': email,
            'password': self.password, 'next': 'clips',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/clips')
        return client

    def test_registration_explains_one_account_and_needs_no_owner_password(self):
        client = self.web.app.test_client()
        response = client.get('/account?mode=register&next=clips')
        self.assertIn(b'Create your Auto Poster AI account', response.data)
        self.assertIn(b'One account for your posts, Clip Studio', response.data)
        self.assertIn(b'Use at least 12 characters', response.data)
        self.assertNotIn(b'Owner dashboard', response.data)
        old_login=client.get('/login')
        self.assertEqual(old_login.status_code,200)
        self.assertIn(b'Sign in with my email',old_login.data)
        self.assertIn(b'Create my account',old_login.data)
        self.register()
        self.assertEqual(client.post('/login', data={'password':'unknown'}).status_code, 503)

    def test_homepage_leads_with_a_working_plan_when_video_is_unavailable(self):
        import clip_studio_web
        from bs4 import BeautifulSoup
        client=self.web.app.test_client()
        with patch.object(clip_studio_web,'renderer_readiness',return_value={'ready':False}),patch('app_mvp.preview_mode',return_value=False):
            page=BeautifulSoup(client.get('/welcome').data,'html.parser')
            self.assertEqual(page.select_one('.hero .actions a')['href'],'/launch')
            self.assertIsNone(page.select_one('.hero a[href="/clips"]'))
        with patch.object(clip_studio_web,'renderer_readiness',return_value={'ready':True}),patch('app_mvp.preview_mode',return_value=False):
            page=BeautifulSoup(client.get('/welcome').data,'html.parser')
            self.assertEqual(page.select_one('.hero .actions a')['href'],'/clips')

    def test_missing_confirmation_service_offers_working_next_steps(self):
        client=self.register()
        with patch('account_support.mail_configured',return_value=False),patch.dict(os.environ,{'AUTOPOST_PREVIEW_MODE':'false'}):
            response=client.get('/clips')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Build my first plan',response.data)
        self.assertIn(b'Account confirmation is being prepared',response.data)
        self.assertNotIn(b'>Confirm my email</a>',response.data)

    def test_saved_brand_and_post_draft_return_after_fresh_client_login(self):
        client = self.register()
        token = self.csrf(client)
        saved = client.post('/clips/brand', data={
            '_csrf': token, 'revision': '0', 'name': 'My Garden Business',
            'accent': '#337744',
        })
        self.assertEqual(saved.status_code, 200, saved.data)
        draft = client.post('/studio/draft', data={
            '_csrf': token, 'revision': '0', 'content': 'My first private post',
        })
        self.assertEqual(draft.status_code, 200, draft.data)
        with client.session_transaction() as session:
            original_identity = session['account_id']
        client.get('/logout')
        self.assertEqual(client.get('/clips/brand').status_code, 401)

        returned = self.web.app.test_client()
        returned.get('/account?mode=login&next=clips')
        signed_in = returned.post('/account', data={
            '_csrf': self.csrf(returned), 'mode': 'login',
            'email': 'NEW@example.test', 'password': self.password, 'next': 'clips',
        })
        self.assertEqual(signed_in.location, '/clips')
        with returned.session_transaction() as session:
            self.assertEqual(session['account_id'], original_identity)
        self.assertEqual(returned.get('/clips/brand').json['profile']['name'], 'My Garden Business')
        self.assertEqual(returned.get('/studio/draft').json['content'], 'My first private post')
        for path in ('/', '/studio', '/clips'):
            self.assertEqual(returned.get(path).status_code, 200, path)

    def test_other_customer_cannot_read_first_customers_brand_or_draft(self):
        first = self.register('first@example.test')
        token = self.csrf(first)
        first.post('/clips/brand', data={
            '_csrf':token,'revision':'0','name':'Private first brand','accent':'#AABBCC',
        })
        first.post('/studio/draft', data={'_csrf':token,'revision':'0','content':'Private first draft'})
        second = self.register('second@example.test')
        self.assertEqual(second.get('/clips/brand').json['profile']['name'], '')
        self.assertEqual(second.get('/studio/draft').json['content'], '')
        self.assertEqual(second.get('/clips/jobs').json['jobs'], [])
        with self.web.db() as connection:
            rows = connection.execute('SELECT password_hash,workspace_id FROM customer_accounts').fetchall()
        self.assertEqual(len({row['workspace_id'] for row in rows}), 2)
        self.assertTrue(all(row['password_hash'].startswith('scrypt:') for row in rows))
        self.assertTrue(all(self.password not in row['password_hash'] for row in rows))
        self.assertNotEqual(rows[0]['password_hash'], rows[1]['password_hash'])

    def test_wsgi_customer_service_starts_without_optional_owner_login(self):
        sys.modules.pop('app_mvp_wsgi', None)
        self.addCleanup(lambda: sys.modules.pop('app_mvp_wsgi', None))
        with patch('threading.Thread') as thread:
            module = importlib.import_module('app_mvp_wsgi')
        self.assertIs(module.app, self.web.app)
        thread.return_value.start.assert_called_once()
        self.assertTrue(module.app.config['SESSION_COOKIE_SECURE'])

    def test_wsgi_rejects_weak_configured_owner_password_and_session_secret(self):
        for values in ({'AUTOPOST_APP_PASSWORD':'short'}, {'AUTOPOST_WEB_SECRET':'short'}):
            sys.modules.pop('app_mvp_wsgi', None)
            with patch.dict(os.environ, values), patch('threading.Thread') as thread:
                with self.assertRaises(RuntimeError):
                    importlib.import_module('app_mvp_wsgi')
                thread.return_value.start.assert_not_called()
        sys.modules.pop('app_mvp_wsgi', None)


if __name__ == '__main__':
    unittest.main()
