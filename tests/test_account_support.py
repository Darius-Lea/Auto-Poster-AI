"""Offline recovery, session revocation, privacy export and account deletion checks."""
from __future__ import annotations
import importlib,json,os,smtplib,tempfile,time,unittest
from unittest.mock import patch
import customer_accounts
import account_support

class AccountSupportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.env=patch.dict(os.environ,{'AUTOPOST_WEB_DB':self.tmp.name+'/app.db','AUTOPOST_APP_PASSWORD':'synthetic-owner-long-password','AUTOPOST_WEB_SECRET':'synthetic-session-secret-more-than-thirty-two','AUTOPOST_REQUIRE_PAYMENT':'false','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_PUBLIC_URL':'https://app.example.test','AUTOPOST_SMTP_HOST':'smtp.example.test','AUTOPOST_SMTP_PORT':'587','AUTOPOST_SMTP_FROM':'support@example.test','AUTOPOST_SMTP_SECURITY':'starttls'},clear=False);self.env.start();self.addCleanup(self.env.stop)
  import app_mvp
  self.web=importlib.reload(app_mvp);self.web.init_db();self.web.app.config['TESTING']=True
  if 'account_support' not in self.web.app.blueprints:self.web.app.register_blueprint(account_support.account_support_bp)
  self.a,self.ua=self.create('a@example.test','Customer A');self.b,self.ub=self.create('b@example.test','Customer B')
 def csrf(self,c):
  with c.session_transaction() as s:return s['csrf']
 def create(self,email,name):
  c=self.web.app.test_client();c.get('/account')
  r=c.post('/account',data={'_csrf':self.csrf(c),'mode':'register','email':email,'name':name,'password':'synthetic-customer-password'})
  self.assertEqual(r.status_code,302)
  with c.session_transaction() as s:user=customer_accounts.get(s['account_id'])
  return c,user
 def post(self,c,path,**values):return c.post(path,data={'_csrf':self.csrf(c),**values})
 def test_verification_is_bound_expiring_one_use_and_separate_from_reset(self):
  self.assertFalse(customer_accounts.get(self.ua['id'])['email_verified'])
  verification=customer_accounts.create_verification_token(self.ua['id'],now=1000)
  reset=customer_accounts.create_reset_token(self.ua['email'],now=1000)
  with customer_accounts.connect() as c:row=c.execute('SELECT * FROM customer_email_verifications').fetchone()
  self.assertNotEqual(row['token_hash'],verification['token']);self.assertEqual(row['email'],self.ua['email'])
  with self.assertRaises(ValueError):customer_accounts.verify_email(reset['token'],now=1100)
  with self.assertRaises(ValueError):customer_accounts.verify_email(verification['token'],now=2800)
  customer_accounts.verify_email(verification['token'],now=1100)
  self.assertTrue(customer_accounts.get(self.ua['id'])['email_verified']);self.assertFalse(customer_accounts.get(self.ub['id'])['email_verified'])
  self.assertEqual(customer_accounts.get(self.ua['id'])['auth_version'],0)
  with self.assertRaises(ValueError):customer_accounts.verify_email(verification['token'],now=1101)
  self.assertIsNone(customer_accounts.create_verification_token(self.ua['id'],now=1102))
  customer_accounts.reset_password(reset['token'],'replacement-verified-password',now=1103)
  self.assertTrue(customer_accounts.get(self.ua['id'])['email_verified'])
 def test_verification_rejects_email_change_and_cross_purpose_tokens(self):
  token=customer_accounts.create_verification_token(self.ua['id'])
  with customer_accounts.connect() as c:c.execute("UPDATE customer_accounts SET email='changed@example.test' WHERE id=?",(self.ua['id'],))
  with self.assertRaises(ValueError):customer_accounts.verify_email(token['token'])
  with self.assertRaises(ValueError):customer_accounts.reset_password(token['token'],'replacement-long-password')
 def test_verification_email_and_confirmation_routes_are_safe(self):
  with patch('account_support._deliver_verification') as deliver:
   r=self.post(self.a,'/account/send-verification');self.assertEqual(r.status_code,302)
  deliver.assert_called_once();self.assertEqual(deliver.call_args.args[0],self.ua['email']);token=deliver.call_args.args[1]
  self.assertNotIn(token.encode(),r.data)
  anonymous=self.web.app.test_client();self.assertEqual(anonymous.get('/account/verify').status_code,200)
  self.assertEqual(anonymous.post('/account/verify',data={'token':token}).status_code,403)
  confirmed=self.post(anonymous,'/account/verify',token=token);self.assertEqual(confirmed.status_code,200)
  self.assertIn(b'Your email is confirmed',confirmed.data);self.assertFalse(customer_accounts.get(self.ub['id'])['email_verified'])
  self.assertIn(b'Your email is confirmed',self.a.get('/account/settings').data)
 def test_verification_requests_are_limited_and_missing_mail_is_honest(self):
  with patch('account_support._deliver_verification') as deliver:
   for _ in range(4):self.post(self.a,'/account/send-verification')
   self.assertEqual(deliver.call_count,3)
  with patch.dict(os.environ,{'AUTOPOST_SMTP_HOST':''}),patch('account_support._deliver_verification') as deliver:
   response=self.post(self.b,'/account/send-verification')
   self.assertEqual(response.status_code,302);deliver.assert_not_called()
   self.assertIn(b'not configured',self.b.get('/account/settings').data)
 def test_reset_tokens_are_hashed_expiring_and_single_use(self):
  reset=customer_accounts.create_reset_token(self.ua['email'],now=1000)
  with customer_accounts.connect() as c:stored=c.execute('SELECT * FROM customer_password_resets').fetchone()
  self.assertNotEqual(stored['token_hash'],reset['token']);self.assertNotIn(reset['token'],str(dict(stored)))
  with self.assertRaises(ValueError):customer_accounts.reset_password(reset['token'],'new-password-long-enough',now=2800)
  customer_accounts.reset_password(reset['token'],'new-password-long-enough',now=1100)
  with self.assertRaises(ValueError):customer_accounts.reset_password(reset['token'],'another-password-long-enough',now=1101)
  self.assertIsNone(customer_accounts.login(self.ua['email'],'synthetic-customer-password'))
  self.assertEqual(customer_accounts.login(self.ua['email'],'new-password-long-enough')['auth_version'],1)
  self.assertEqual(customer_accounts.get(self.ub['id'])['auth_version'],0)
 def test_recovery_public_response_does_not_enumerate_or_reveal_token(self):
  c=self.web.app.test_client();self.assertEqual(c.get('/account/forgot').status_code,200)
  with patch('account_support._deliver_reset') as deliver:
   known=self.post(c,'/account/forgot',email=self.ua['email']);unknown=self.post(c,'/account/forgot',email='missing@example.test')
  self.assertEqual(known.status_code,200);self.assertEqual(known.data,unknown.data)
  deliver.assert_called_once();token=deliver.call_args.args[1];self.assertNotIn(token.encode(),known.data)
  self.assertNotIn(self.ua['email'].encode(),known.data)
  self.assertEqual(known.headers['Cache-Control'],'no-store')
  self.assertEqual(c.post('/account/forgot',data={'email':self.ua['email']}).status_code,403)
 def test_mail_setup_is_honest_and_delivery_failure_stays_generic(self):
  with patch.dict(os.environ,{'AUTOPOST_SMTP_HOST':''}):
   self.assertFalse(account_support.mail_configured());c=self.web.app.test_client();c.get('/account/forgot');r=self.post(c,'/account/forgot',email=self.ua['email']);self.assertEqual(r.status_code,503);self.assertIn(b'not available',r.data)
  with patch.dict(os.environ,{'AUTOPOST_PUBLIC_URL':'https://app.example.test/unsafe/path'}):self.assertFalse(account_support.mail_configured())
  with patch.dict(os.environ,{'AUTOPOST_SMTP_FROM':'good@example.test\r\nBcc:bad@example.test'}):self.assertFalse(account_support.mail_configured())
  c=self.web.app.test_client();c.get('/account/forgot')
  with patch('account_support._deliver_reset',side_effect=smtplib.SMTPException('do not expose')):
   known=self.post(c,'/account/forgot',email=self.ua['email']);unknown=self.post(c,'/account/forgot',email='missing@example.test')
  self.assertEqual(known.data,unknown.data);self.assertNotIn(b'do not expose',known.data)
 def test_reset_and_change_password_revoke_old_sessions(self):
  reset=customer_accounts.create_reset_token(self.ua['email'])
  c=self.web.app.test_client();self.assertEqual(c.get('/account/reset').status_code,200)
  r=self.post(c,'/account/reset',token=reset['token'],password='replacement-password-123')
  self.assertEqual(r.status_code,302);self.assertEqual(self.a.get('/studio').status_code,401)
  self.assertEqual(self.b.get('/studio').status_code,200)
  self.a.get('/account?mode=login')
  self.post(self.a,'/account',mode='login',email=self.ua['email'],password='replacement-password-123')
  r=self.post(self.a,'/account/change-password',current_password='replacement-password-123',password='new-changed-password-123')
  self.assertEqual(r.status_code,302);self.assertEqual(customer_accounts.get(self.ua['id'])['auth_version'],2)
  self.assertEqual(self.a.get('/studio').status_code,401)
 def test_export_has_only_own_records_and_no_connection_secrets(self):
  with self.web.db() as c:
   c.execute("INSERT INTO app_posts(content,platform,due_at,workspace_id) VALUES('A private content','draft','2030-01-01',?)",(self.ua['workspace_id'],))
   c.execute("INSERT INTO app_posts(content,platform,due_at,workspace_id) VALUES('B private content','draft','2030-01-01',?)",(self.ub['workspace_id'],))
  r=self.post(self.a,'/account/export');self.assertEqual(r.status_code,200)
  self.assertIn(b'A private content',r.data);self.assertNotIn(b'B private content',r.data);self.assertNotIn(b'password_hash',r.data);self.assertNotIn(b'auth_version',r.data);self.assertNotIn(b'credentials',r.data)
  self.assertEqual(self.web.app.test_client().get('/account/settings').status_code,401)
 def test_delete_confirms_password_blocks_unsettled_billing_and_preserves_other_customer(self):
  ws=self.ua['workspace_id']
  with self.web.db() as c:
   c.execute("INSERT INTO app_posts(content,platform,due_at,workspace_id) VALUES('A delete me','draft','2030-01-01',?)",(ws,))
   c.execute("INSERT INTO app_posts(content,platform,due_at,workspace_id) VALUES('B retain me','draft','2030-01-01',?)",(self.ub['workspace_id'],))
   c.execute("INSERT INTO subscriptions VALUES(?,'stripe','cus_a','sub_a','active','basic',?,1,1)",(ws,int(time.time())+3600))
  with self.assertRaisesRegex(ValueError,'Type DELETE'):account_support.delete_customer(self.ua,'synthetic-customer-password','wrong')
  with self.assertRaisesRegex(ValueError,'password'):account_support.delete_customer(self.ua,'wrong-password','DELETE')
  with self.assertRaisesRegex(ValueError,'Cancel'):account_support.delete_customer(self.ua,'synthetic-customer-password','DELETE')
  with self.web.db() as c:c.execute("UPDATE subscriptions SET status='canceled' WHERE workspace_id=?",(ws,))
  own_confirmation=customer_accounts.create_verification_token(self.ua['id'])
  other_confirmation=customer_accounts.create_verification_token(self.ub['id'])
  r=self.post(self.a,'/account/delete',current_password='synthetic-customer-password',confirmation='DELETE');self.assertEqual(r.status_code,200)
  with self.assertRaises(ValueError):customer_accounts.verify_email(own_confirmation['token'])
  customer_accounts.verify_email(other_confirmation['token'])
  self.assertIsNone(customer_accounts.get(self.ua['id']));self.assertIsNotNone(customer_accounts.get(self.ub['id']))
  with self.web.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM app_posts WHERE workspace_id=?',(ws,)).fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM app_posts WHERE workspace_id=?',(self.ub['workspace_id'],)).fetchone()[0],1)
   self.assertEqual(c.execute('SELECT count(*) FROM subscriptions WHERE workspace_id=?',(ws,)).fetchone()[0],1)
  self.assertEqual(self.a.get('/studio').status_code,401)
 def test_delete_refuses_pending_checkout(self):
  import app_control
  with patch.dict(os.environ,{'STRIPE_BASIC_PRICE_ID':'price_basic_test'}):app_control.checkout_attempt(self.ua['workspace_id'],'basic')
  with self.assertRaisesRegex(ValueError,'checkout'):account_support.delete_customer(self.ua,'synthetic-customer-password','DELETE')
 def test_expired_checkout_requires_provider_confirmation_not_timer(self):
  from types import SimpleNamespace
  import app_control
  with patch.dict(os.environ,{'STRIPE_BASIC_PRICE_ID':'price_basic_test'}):attempt=app_control.checkout_attempt(self.ua['workspace_id'],'basic')
  with self.web.db() as c:c.execute("UPDATE billing_checkout_attempts SET expires_at=1,session_id='cs_test_example' WHERE workspace_id=?",(self.ua['workspace_id'],))
  payload={'id':'cs_test_example','client_reference_id':self.ua['workspace_id'],'status':'complete','payment_status':'paid','subscription':'sub_unconfirmed'}
  with patch.dict(os.environ,{'STRIPE_SECRET_KEY':'sk_test_fixture'}),patch('account_support.requests.get',return_value=SimpleNamespace(status_code=200,json=lambda:payload)) as fetch:
   with self.assertRaisesRegex(ValueError,'checkout'):account_support.delete_customer(self.ua,'synthetic-customer-password','DELETE')
   payload.update(status='expired',payment_status='unpaid',subscription=None)
   self.assertTrue(account_support.delete_customer(self.ua,'synthetic-customer-password','DELETE'))
   self.assertEqual(fetch.call_count,2)
  self.assertIsNone(customer_accounts.get(self.ua['id']))
 def test_delete_refuses_inflight_publication(self):
  with self.web.db() as c:c.execute("INSERT INTO app_posts(content,platform,due_at,status,workspace_id) VALUES('Sending','facebook_page','2030-01-01','sending',?)",(self.ua['workspace_id'],))
  with self.assertRaisesRegex(ValueError,'currently being delivered'):account_support.delete_customer(self.ua,'synthetic-customer-password','DELETE')

if __name__=='__main__':unittest.main()
