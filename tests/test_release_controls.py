"""Release controls: actual pause enforcement and safe, idempotent billing paths."""
import importlib,os,tempfile,time,unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
import app_control


def event(status='active',created=100,price=None,plan=None):
 obj={'id':'sub_release','customer':'cus_release','status':status,'current_period_end':int(time.time())+86400,'metadata':{'workspace_id':'owner'}}
 if plan:obj['metadata']['plan']=plan
 if price:obj['items']={'data':[{'price':{'id':price},'current_period_end':int(time.time())+86400}]}
 return {'type':'customer.subscription.updated','created':created,'data':{'object':obj}}


class ReleaseControlsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'AUTOPOST_WEB_DB':self.temp.name+'/app.db','AUTOPOST_APP_PASSWORD':'synthetic-owner-password123','AUTOPOST_WEB_SECRET':'synthetic-release-secret-longer-than-thirty-two','AUTOPOST_REQUIRE_PAYMENT':'false','AUTOPOST_PREVIEW_MODE':'false','AUTOPOST_PUBLIC_URL':'https://app.example.test','STRIPE_SECRET_KEY':'sk_test_fixture','STRIPE_BASIC_PRICE_ID':'price_basic','STRIPE_CREATOR_PRICE_ID':'','STRIPE_BUSINESS_PRICE_ID':'','STRIPE_AGENCY_PRICE_ID':'','AUTOPOST_PUBLIC_SALES_ENABLED':'false'})
  self.env.start();import app_mvp;self.web=importlib.reload(app_mvp);self.web.init_db();self.web.app.config['TESTING']=True
  self.client=self.web.app.test_client();self.client.post('/login',data={'password':'synthetic-owner-password123'})
  with self.client.session_transaction() as s:self.csrf=s['csrf']
 def tearDown(self):self.env.stop();self.temp.cleanup()
 def add_post(self,platform='facebook',workspace='owner',status='scheduled',due=None):
  due=due or (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
  with self.web.db() as c:return c.execute('INSERT INTO app_posts(content,platform,due_at,status,workspace_id) VALUES(?,?,?,?,?)',('Approved content',platform,due,status,workspace)).lastrowid
 def customer(self):
  c=self.web.app.test_client();c.get('/account')
  with c.session_transaction() as s:csrf=s['csrf']
  r=c.post('/account',data={'_csrf':csrf,'mode':'register','email':'release@example.test','password':'synthetic-customer-password123','name':'Customer'})
  self.assertEqual(r.status_code,302)
  with self.web.db() as connection:connection.execute('UPDATE customer_accounts SET email_verified=1')
  with c.session_transaction() as s:
   import customer_accounts
   return c,s['csrf'],customer_accounts.get(s['account_id'])['workspace_id']
 def test_pause_holds_real_job_and_resume_can_publish(self):
  post=self.add_post();app_control.set_publishing_paused('owner',True)
  with patch.object(self.web,'publish_to_facebook',return_value='meta:123_456') as publisher:
   self.assertEqual(self.web.run_due(),0);publisher.assert_not_called()
   with self.web.db() as c:self.assertEqual(c.execute('SELECT status FROM app_posts WHERE id=?',(post,)).fetchone()[0],'scheduled')
   app_control.set_publishing_paused('owner',False);self.assertEqual(self.web.run_due(),1);publisher.assert_called_once()
 def test_pause_between_claim_and_send_requeues_without_publication(self):
  post=self.add_post(status='sending')
  with self.web.db() as c:job=dict(c.execute('SELECT * FROM app_posts WHERE id=?',(post,)).fetchone())
  app_control.set_publishing_paused('owner',True)
  with patch.object(self.web,'claim_due',side_effect=[job,None]),patch.object(self.web,'publish_to_facebook',side_effect=AssertionError('published while paused')):
   self.web.run_due()
  with self.web.db() as c:self.assertEqual(c.execute('SELECT status FROM app_posts WHERE id=?',(post,)).fetchone()[0],'scheduled')
 def test_draft_backlog_cannot_starve_external_publication(self):
  for _ in range(110):self.add_post('draft',due=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
  external=self.add_post();self.assertEqual(self.web.claim_due()['id'],external)
  r=self.client.post('/schedule',data={'_csrf':self.csrf,'content':'Another draft','destinations':'draft','when':'now','frequency':'once','time_zone':'UTC'})
  self.assertEqual(r.status_code,429)
 def test_customer_pause_is_scoped_and_settings_hide_legacy_controls(self):
  c,csrf,ws=self.customer();self.assertEqual(c.post('/publishing/pause',data={'_csrf':csrf,'paused':'1'}).status_code,302)
  self.assertTrue(app_control.publishing_paused(ws));self.assertFalse(app_control.publishing_paused('owner'))
  body=c.get('/control').get_data(as_text=True);self.assertIn('Resume publishing',body);self.assertNotIn('Booking photos',body);self.assertNotIn('GitHub Actions control bridge',body)
  self.assertEqual(c.post('/publishing/pause',data={'paused':'0'}).status_code,403)
 def test_checkout_double_tap_reuses_session_and_price_is_server_selected(self):
  checkout=SimpleNamespace(id='cs_test_release',url='https://checkout.stripe.com/c/pay/release')
  with patch('stripe.checkout.Session.create',return_value=checkout) as create:
   for _ in range(2):self.assertEqual(self.client.post('/billing/checkout',data={'_csrf':self.csrf,'plan':'basic','price':'price_attacker'}).status_code,303)
   create.assert_called_once();args=create.call_args.kwargs;self.assertEqual(args['line_items'][0]['price'],'price_basic');self.assertTrue(args['idempotency_key'])
 def test_checkout_network_uncertainty_retries_same_idempotency_key(self):
  import stripe
  checkout=SimpleNamespace(id='cs_test_retry',url='https://checkout.stripe.com/c/pay/retry')
  with patch('stripe.checkout.Session.create',side_effect=[stripe.APIConnectionError('offline'),checkout]) as create:
   self.assertEqual(self.client.post('/billing/checkout',data={'_csrf':self.csrf,'plan':'basic'}).status_code,302)
   self.assertEqual(self.client.post('/billing/checkout',data={'_csrf':self.csrf,'plan':'basic'}).status_code,303)
   self.assertEqual(create.call_args_list[0].kwargs['idempotency_key'],create.call_args_list[1].kwargs['idempotency_key'])
 def test_existing_subscription_does_not_open_another_checkout(self):
  app_control.record_subscription_event(event())
  with patch('stripe.checkout.Session.create',side_effect=AssertionError('duplicate subscription')):
   self.assertEqual(self.client.post('/billing/checkout',data={'_csrf':self.csrf}).status_code,302)
 def test_paid_customer_sales_are_closed_until_operator_enables_them(self):
  c,csrf,ws=self.customer()
  with patch('stripe.checkout.Session.create',side_effect=AssertionError('charged before readiness')):
   self.assertEqual(c.post('/billing/checkout',data={'_csrf':csrf,'plan':'basic'}).status_code,409)
  with patch.dict(os.environ,{'AUTOPOST_PUBLIC_SALES_ENABLED':'true'}),patch('service_readiness.configured',return_value={'public_sales_enabled':True,'configuration_complete':True}),patch('stripe.checkout.Session.create',return_value=SimpleNamespace(id='cs_customer',url='https://checkout.stripe.com/c/pay/customer')) as create:
   self.assertEqual(c.post('/billing/checkout',data={'_csrf':csrf,'plan':'basic'}).status_code,303);self.assertEqual(create.call_args.kwargs['client_reference_id'],ws)
 def test_tier_comes_from_configured_price_not_customer_metadata(self):
  with patch.dict(os.environ,{'STRIPE_CREATOR_PRICE_ID':'price_creator'}):
   self.assertTrue(app_control.record_subscription_event(event(price='price_creator')));self.assertEqual(app_control.subscription_details()['plan'],'creator')
   app_control.record_subscription_event(event(created=101,price='price_unknown',plan='agency'));self.assertEqual(app_control.subscription_details()['plan'],'basic')
 def test_equal_timestamp_active_webhook_cannot_undo_cancellation(self):
  app_control.record_subscription_event(event(created=100));app_control.record_subscription_event(event('canceled',created=100));app_control.record_subscription_event(event(created=100));self.assertFalse(app_control.subscription_details()['active'])
  app_control.record_subscription_event(event(created=101));self.assertTrue(app_control.subscription_details()['active'])
 def test_public_origin_rejects_credentials_and_non_origin_components(self):
  for origin in ['http://app.example.test','https://person:secret@app.example.test','https://app.example.test/path','https://app.example.test?x=1','https://app.example.test#x']:
   with self.subTest(origin=origin),patch.dict(os.environ,{'AUTOPOST_PUBLIC_URL':origin}):
    with self.assertRaises(ValueError):app_control.public_origin()
 def test_cancelled_checkout_plan_can_only_be_changed_after_expiry(self):
  first=app_control.checkout_attempt('owner','basic',now=100)
  with patch.dict(os.environ,{'STRIPE_CREATOR_PRICE_ID':'price_creator'}):
   with self.assertRaises(ValueError):app_control.checkout_attempt('owner','creator',now=101)
   second=app_control.checkout_attempt('owner','creator',now=first['expires_at'])
   self.assertNotEqual(first['idempotency_key'],second['idempotency_key'])
 def test_ambiguous_prices_and_malformed_signed_payload_do_not_grant_higher_tier(self):
  with patch.dict(os.environ,{'STRIPE_CREATOR_PRICE_ID':'price_basic'}):
   self.assertFalse(app_control.billing_plans()['creator']['configured'])
   app_control.record_subscription_event(event(price='price_basic'));self.assertEqual(app_control.subscription_details()['plan'],'basic')
  malformed=event();malformed['data']['object']['items']={'data':[None]};self.assertFalse(app_control.record_subscription_event(malformed))
 def test_ready_flag_alone_cannot_sell_without_recovery_and_support(self):
  c,csrf,ws=self.customer()
  with patch.dict(os.environ,{'AUTOPOST_PUBLIC_SALES_ENABLED':'true','AUTOPOST_SMTP_HOST':'','AUTOPOST_SUPPORT_EMAIL':''}),patch('meta_accounts.configured',return_value=True),patch('stripe.checkout.Session.create',side_effect=AssertionError('charged with incomplete service')):
   self.assertEqual(c.post('/billing/checkout',data={'_csrf':csrf,'plan':'basic'}).status_code,409)
 def test_browser_login_preserves_known_destination_without_open_redirect(self):
  c=self.web.app.test_client()
  self.assertEqual(c.get('/studio').status_code,401)
  response=c.get('/studio',headers={'Accept':'text/html'});self.assertEqual(response.status_code,302);self.assertIn('next=studio',response.location)
  self.assertEqual(self.client.get('/account?next=https://attacker.invalid').location,'/')
  self.assertEqual(self.client.get('/account?next=launch').location,'/launch')
 def test_auth_version_change_revokes_existing_customer_session(self):
  c,csrf,ws=self.customer()
  with self.web.db() as db:db.execute('UPDATE customer_accounts SET auth_version=auth_version+1 WHERE workspace_id=?',(ws,))
  self.assertEqual(c.get('/control').status_code,401)
if __name__=='__main__':unittest.main()
