"""Customer signup and tenant isolation, with all outbound publishing mocked."""
from __future__ import annotations
import importlib
import json
import os
import tempfile
import time
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch


class CustomerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{
            'AUTOPOST_WEB_DB':self.tmp.name+'/customers.db',
            'AUTOPOST_APP_PASSWORD':'synthetic-owner-password',
            'AUTOPOST_WEB_SECRET':'synthetic-customer-session-secret-longer-than-32',
            'AUTOPOST_REQUIRE_PAYMENT':'false','AUTOPOST_PREVIEW_MODE':'false',
            'AUTOPOST_CUSTOMER_SIGNUP_ENABLED':'true',
            'META_PAGES_ENCRYPTION_SECRET':'synthetic-encryption-key-longer-than-32',
            'META_PAGE_ID':'999','META_PAGE_ACCESS_TOKEN':'synthetic-owner-token',
        });self.env.start();self.addCleanup(self.env.stop)
        import app_mvp,customer_accounts,meta_accounts,app_control
        self.web=importlib.reload(app_mvp);self.web.init_db();self.web.app.config['TESTING']=True
        self.accounts=customer_accounts;self.meta=meta_accounts;self.control=app_control
        self.a=self.web.app.test_client();self.b=self.web.app.test_client()
        self.wa=self.register(self.a,'a@example.test','Customer A')
        self.wb=self.register(self.b,'b@example.test','Customer B')
        with self.accounts.connect() as c:c.execute('UPDATE customer_accounts SET email_verified=1')

    def csrf(self,client):
        with client.session_transaction() as sess:return sess['csrf']

    def register(self,client,email,name):
        self.assertEqual(client.get('/account?next=setup').status_code,200)
        r=client.post('/account',data={'_csrf':self.csrf(client),'mode':'register','email':email,'password':'synthetic-customer-password','name':name,'next':'setup'})
        self.assertEqual(r.status_code,302,r.get_data(as_text=True));self.assertEqual(r.location,'/?finish_setup=1')
        with client.session_transaction() as sess:return self.accounts.get(sess['account_id'])['workspace_id']

    def post(self,client,path,**data):return client.post(path,data={'_csrf':self.csrf(client),**data})

    def test_accounts_use_unique_workspaces_and_salted_hashes(self):
        self.assertNotEqual(self.wa,self.wb);self.assertNotEqual(self.wa,'owner')
        with self.accounts.connect() as c:rows=c.execute('SELECT * FROM customer_accounts ORDER BY email').fetchall()
        self.assertTrue(all(r['password_hash'].startswith('scrypt:') for r in rows))
        self.assertNotEqual(rows[0]['password_hash'],rows[1]['password_hash']);self.assertNotIn('synthetic-customer-password',str([dict(r) for r in rows]))
        self.assertIsNone(self.accounts.login('a@example.test','wrong-password'))
        self.assertEqual(self.accounts.login('A@EXAMPLE.TEST','synthetic-customer-password')['workspace_id'],self.wa)

    def test_signup_and_login_require_csrf_and_limit_requests(self):
        anonymous=self.web.app.test_client();anonymous.get('/account')
        self.assertEqual(anonymous.post('/account',data={'mode':'register'}).status_code,403)
        self.assertEqual(anonymous.post('/account',data={'mode':'register','_csrf':'invalid-☃'}).status_code,403)
        self.assertEqual(anonymous.post('/login',data={'password':'invalid-☃'}).status_code,401)
        for i in range(12):self.accounts.attempt('fixture','address',now=1000)
        with self.assertRaises(PermissionError):self.accounts.attempt('fixture','address',now=1000)
        self.accounts.attempt('fixture','address',now=1301)
        self.a.get('/logout');self.a.get('/account?mode=login')
        r=self.post(self.a,'/account',mode='login',email='a@example.test',password='synthetic-customer-password')
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.a.get('/').status_code,200)

    def test_posts_and_cancellation_are_scoped_even_with_forged_workspace(self):
        self.assertEqual(self.post(self.a,'/schedule',content='Private A draft',destinations='draft',when='now',time_zone='UTC',workspace_id=self.wb).status_code,302)
        self.assertNotIn(b'Private A draft',self.b.get('/').data)
        with self.web.db() as c:row=c.execute('SELECT * FROM app_posts').fetchone()
        self.assertEqual(row['workspace_id'],self.wa)
        self.assertEqual(self.post(self.b,'/cancel/'+str(row['id'])).status_code,404)
        self.assertEqual(self.post(self.b,'/activate/'+str(row['id'])).status_code,404)
        self.assertEqual(self.post(self.a,'/cancel/'+str(row['id'])).status_code,302)

    def test_owner_rows_migrate_and_stay_private(self):
        with self.web.db() as c:c.execute("INSERT INTO app_posts(content,platform,due_at) VALUES('Owner private draft','draft',?)",(datetime.now(timezone.utc).isoformat(),))
        self.assertNotIn(b'Owner private draft',self.a.get('/').data)
        owner=self.web.app.test_client();owner.post('/login',data={'password':'synthetic-owner-password'})
        self.assertIn(b'Owner private draft',owner.get('/').data)
        self.assertNotIn(b'Existing Facebook Page',self.a.get('/').data)

    def test_customer_cannot_use_owner_env_token_and_worker_never_uses_it(self):
        self.post(self.a,'/schedule',content='Not an owner post',destinations='facebook',when='now',time_zone='UTC')
        with self.web.db() as c:
            row=c.execute('SELECT * FROM app_posts WHERE workspace_id=?',(self.wa,)).fetchone()
            self.assertNotEqual(row['status'],'scheduled')
            c.execute("UPDATE app_posts SET status='scheduled' WHERE id=?",(row['id'],))
        with patch.object(self.web,'publish_to_facebook',side_effect=AssertionError('Customer used owner token')) as publisher:
            self.assertEqual(self.web.run_due(),1);publisher.assert_not_called()
        with self.web.db() as c:self.assertEqual(c.execute('SELECT status FROM app_posts WHERE id=?',(row['id'],)).fetchone()['status'],'needs_connection')

    def test_pages_scheduling_and_repeat_keep_customer_identity(self):
        self.meta.remember_pending(self.wa,[{'page_id':'111','name':'A Page','token':'synthetic-a-page-token','tasks':['CREATE_CONTENT']}]);self.meta.activate_pages(self.wa,['111'])
        self.assertIn(b'A Page',self.a.get('/').data);self.assertNotIn(b'A Page',self.b.get('/').data)
        self.assertEqual(self.post(self.b,'/schedule',content='Wrong Page',facebook_pages='111',when='now',time_zone='UTC').status_code,400)
        self.assertEqual(self.post(self.a,'/schedule',content='A Page update',facebook_pages='111',when='now',time_zone='UTC',frequency='daily').status_code,302)
        with patch.object(self.meta,'publish_message',return_value='meta:111_123') as publish:
            self.assertEqual(self.web.run_due(),1);publish.assert_called_once_with(self.wa,'111','A Page update')
        with self.web.db() as c:
            rows=c.execute('SELECT workspace_id,status FROM app_posts ORDER BY id').fetchall();series=c.execute('SELECT workspace_id FROM app_series').fetchone()
        self.assertTrue(all(r['workspace_id']==self.wa for r in rows));self.assertEqual(series['workspace_id'],self.wa);self.assertEqual(rows[-1]['status'],'scheduled')

    def test_settings_website_requests_and_automation_plans_are_isolated(self):
        self.control.update_settings({'enabled':1},workspace_id=self.wa)
        self.assertEqual(self.control.get_settings(workspace_id=self.wb)['enabled'],0)
        self.assertEqual(self.control.get_settings()['enabled'],0)
        self.assertEqual(self.post(self.a,'/website',url='https://example.test/feed',topic='A private source',cadence='daily').status_code,302)
        self.assertNotIn(b'A private source',self.b.get('/website').data)
        self.assertEqual(self.post(self.a,'/automations',name='A private automation',topic='A content',kind='manual',destination='facebook_later',cadence='daily',review_mode='approve_first').status_code,302)
        self.assertNotIn(b'A private automation',self.b.get('/automations').data)

    def test_subscription_is_per_customer_and_queue_skips_unpaid_owner(self):
        with patch.dict(os.environ,{'AUTOPOST_REQUIRE_PAYMENT':'true'}):
            event={'type':'customer.subscription.updated','created':100,'data':{'object':{'id':'sub_customer_a','customer':'cus_customer_a','metadata':{'workspace_id':self.wa},'status':'active','current_period_end':int(time.time())+86400}}}
            self.assertTrue(self.control.record_subscription_event(event));self.assertTrue(self.control.allowed(workspace_id=self.wa));self.assertFalse(self.control.allowed(workspace_id=self.wb));self.assertFalse(self.control.allowed())
            self.meta.remember_pending(self.wa,[{'page_id':'111','name':'A Page','token':'synthetic-a-page-token','tasks':['CREATE_CONTENT']}]);self.meta.activate_pages(self.wa,['111'])
            due=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            with self.web.db() as c:
                c.execute("INSERT INTO app_posts(content,platform,due_at,status,workspace_id,page_id) VALUES('Customer A','facebook_page',?,'scheduled',?,'111')",(due,self.wa))
                c.execute("INSERT INTO app_posts(content,platform,due_at,status) VALUES('Owner unpaid','facebook',?,'scheduled')",(due,))
            with patch.object(self.meta,'publish_message',return_value='meta:111_123') as publish:
                self.assertEqual(self.web.run_due(),1);publish.assert_called_once_with(self.wa,'111','Customer A')

    def test_oauth_callback_cannot_cross_customer_session(self):
        with self.a.session_transaction() as sess:sess.update(fb_oauth_workspace=self.wb,fb_oauth_state='fixture-state',fb_oauth_started=int(time.time()))
        self.assertEqual(self.a.get('/facebook/callback?state=fixture-state&code=fixture').status_code,403)

    def test_client_selected_workspace_cannot_change_signed_identity(self):
        with self.a.session_transaction() as sess:sess['workspace_id']='owner'
        self.assertEqual(self.post(self.a,'/schedule',content='Still A',destinations='draft',when='now',time_zone='UTC').status_code,302)
        with self.web.db() as c:self.assertEqual(c.execute('SELECT workspace_id FROM app_posts').fetchone()['workspace_id'],self.wa)


if __name__=='__main__':unittest.main()
