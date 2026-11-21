import importlib,json,os,secrets,tempfile,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

def brief():return dict(name='My Bread Idea',idea='Home baked bread for families who are busy',audience='Busy families nearby',offer='A weekly loaf of bread',difference='Small batches',location='Example town',contact='',stage='idea',goal='feedback',start_date=datetime.now(timezone.utc).date().isoformat())

class LaunchReadinessTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.env=patch.dict(os.environ,{'AUTOPOST_WEB_DB':cls.tmp.name+'/app.db','AUTOPOST_APP_PASSWORD':'test-owner-password-long','AUTOPOST_WEB_SECRET':'test-secret-longer-than-thirty-two-characters','AUTOPOST_PUBLIC_LANDING':'true','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_REQUIRE_PAYMENT':'true'});cls.env.start()
  import app_mvp
  cls.web=importlib.reload(app_mvp);cls.web.init_db();cls.web.app.config['TESTING']=True
 @classmethod
 def tearDownClass(cls):cls.env.stop();cls.tmp.cleanup()
 def account(self,name):
  c=self.web.app.test_client();c.get('/account')
  with c.session_transaction() as s:token=s['csrf']
  self.assertEqual(c.post('/account',data={'_csrf':token,'mode':'register','name':name,'email':name+'@example.test','password':'test-password-really-long'}).status_code,302)
  with c.session_transaction() as s:token=s['csrf']
  return c,token
 def test_an_idea_creates_realistic_free_plan_without_ai_or_payment(self):
  c=self.web.app.test_client();self.assertEqual(c.get('/launch').status_code,200)
  with c.session_transaction() as s:token=s['csrf']
  with patch('ai_providers.generate_text',side_effect=AssertionError('paid AI called')):
   response=c.post('/launch/build',data={**brief(),'_csrf':token})
  self.assertEqual(response.status_code,200);plan=response.get_json();self.assertEqual(len(plan['week']),7);self.assertIn('developing',plan['positioning']);self.assertIn('not AI research',plan['notice']);self.assertNotIn('guaranteed',plan['positioning'])
  self.assertEqual(c.post('/launch/build',data=brief()).status_code,403)
 def test_import_seven_internal_drafts_is_idempotent_and_isolated(self):
  import launch
  a,csrf=self.account('launch-import');b,other=self.account('launch-other');p=launch.build_plan(brief());values={'_csrf':csrf,'brief':json.dumps(brief()),'plan_key':secrets.token_hex(16),'captions':json.dumps([d['caption'] for d in p['week']])}
  first=a.post('/launch/import',data=values);self.assertEqual(first.status_code,200);self.assertEqual(first.get_json()['created'],7)
  self.assertEqual(a.post('/launch/import',data=values).get_json()['created'],0)
  with self.web.db() as db:rows=db.execute("SELECT platform,status FROM app_posts WHERE content LIKE '%My Bread Idea%'").fetchall()
  self.assertTrue(rows);self.assertTrue(all(r['platform']=='draft' and r['status']=='draft_ready' for r in rows))
  self.assertNotIn(b'My Bread Idea',b.get('/').data)
 def test_working_draft_restores_scopes_and_rejects_stale_revision(self):
  a,csrf=self.account('working-draft');b,_=self.account('working-other')
  self.assertEqual(a.post('/studio/draft',data={'_csrf':csrf,'content':'My working caption','revision':0}).status_code,200)
  self.assertEqual(a.get('/studio/draft').get_json()['content'],'My working caption');self.assertEqual(b.get('/studio/draft').get_json()['content'],'')
  self.assertEqual(a.post('/studio/draft',data={'_csrf':csrf,'content':'Stale overwrite','revision':0}).status_code,409)
 def test_public_help_and_recovery_paths_exist_and_readiness_is_private(self):
  c=self.web.app.test_client()
  for path in ['/help','/privacy','/terms','/account/forgot']:self.assertEqual(c.get(path).status_code,200,path)
  self.assertEqual(c.get('/readiness').status_code,401)
  c,csrf=self.account('readiness-customer');self.assertEqual(c.get('/readiness').status_code,403)
 def test_invalid_plan_date_and_secret_bearing_link_are_rejected(self):
  import launch
  for changes in [{'start_date':'yesterday'},{'contact':'https://secret:token@example.test'},{'idea':''}]:
   with self.assertRaises(ValueError):launch.build_plan({**brief(),**changes})
 def test_reviewed_week_can_schedule_atomically_once_with_plan_limits(self):
  import launch
  a,csrf=self.account('launch-schedule');b,other=self.account('launch-schedule-other');data=brief();plan=launch.build_plan(data)
  made=a.post('/launch/import',data={'_csrf':csrf,'brief':json.dumps(data),'plan_key':secrets.token_hex(16),'captions':json.dumps([d['caption'] for d in plan['week']])}).get_json()
  with self.web.db() as connection:connection.execute('UPDATE customer_accounts SET email_verified=1')
  values={'_csrf':csrf,'plan_id':made['plan_id'],'page_id':'123','start_date':(datetime.now(timezone.utc).date()+timedelta(days=2)).isoformat(),'local_time':'12:00','time_zone':'UTC','confirmed':'yes'}
  self.assertEqual(a.post('/launch/schedule',data=values).status_code,400)
  with patch.dict(os.environ,{'AUTOPOST_PREVIEW_MODE':'false'}),patch('app_control.allowed',return_value=True),patch('meta_accounts.get_page_token',return_value='fixture-token'):
   self.assertEqual(b.post('/launch/schedule',data={**values,'_csrf':other}).status_code,400)
   first=a.post('/launch/schedule',data=values);self.assertEqual(first.status_code,200,first.data);self.assertEqual(first.get_json()['scheduled'],7)
   self.assertIn('already saved',a.post('/launch/schedule',data=values).get_json()['message'])
  with self.web.db() as db:rows=db.execute("SELECT * FROM app_posts WHERE page_id='123'").fetchall()
  self.assertEqual(len(rows),7);self.assertTrue(all(r['status']=='scheduled' for r in rows));self.assertEqual(len({r['due_at'][:10] for r in rows}),7)
 def test_scheduled_plan_changes_are_not_silently_dropped(self):
  import launch
  a,csrf=self.account('launch-edit');data=brief();plan=launch.build_plan(data);values={'_csrf':csrf,'brief':json.dumps(data),'plan_key':secrets.token_hex(16),'captions':json.dumps([d['caption'] for d in plan['week']])}
  made=a.post('/launch/import',data=values).get_json()
  with self.web.db() as db:db.execute("UPDATE app_posts SET status='scheduled' WHERE id IN (SELECT post_id FROM business_launch_posts WHERE plan_id=?)",(made['plan_id'],))
  captions=[d['caption'] for d in plan['week']];captions[0]='A change that must not silently replace a scheduled post.'
  self.assertEqual(a.post('/launch/import',data={**values,'captions':json.dumps(captions)}).status_code,400)
 def test_unverified_customer_cannot_connect_or_buy_or_publish(self):
  a,csrf=self.account('launch-unverified')
  with patch('meta_accounts.login_url',side_effect=AssertionError('OAuth started before verification')):
   self.assertEqual(a.get('/facebook/connect').location,'/account/settings')
  with patch('stripe.checkout.Session.create',side_effect=AssertionError('checkout before verification')):
   self.assertEqual(a.post('/billing/checkout',data={'_csrf':csrf}).location,'/account/settings')
  import customer_accounts
  with a.session_transaction() as sess:ws=customer_accounts.get(sess['account_id'])['workspace_id']
  with self.web.db() as db:post=db.execute("INSERT INTO app_posts(content,platform,due_at,status,workspace_id,page_id) VALUES('Verified content','facebook_page',?,'scheduled',?,'123')",((datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),ws)).lastrowid
  with patch.dict(os.environ,{'AUTOPOST_PREVIEW_MODE':'false','AUTOPOST_REQUIRE_PAYMENT':'false'}),patch('meta_accounts.publish_message',side_effect=AssertionError('published without verifiedemail')) as publisher:
   self.web.run_due();publisher.assert_not_called()
  with self.web.db() as db:self.assertEqual(db.execute('SELECT status FROM app_posts WHERE id=?',(post,)).fetchone()['status'],'scheduled')
if __name__=='__main__':unittest.main()
