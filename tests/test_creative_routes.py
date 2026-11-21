"""Workspace integration and preview guards for creative workbench routes."""
import importlib,io,os,tempfile,unittest,zipfile
from unittest.mock import patch
class CreativeRoutesTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.env=patch.dict(os.environ,{'AUTOPOST_WEB_DB':cls.temp.name+'/app.db','AUTOPOST_APP_PASSWORD':'test-owner-long-password','AUTOPOST_WEB_SECRET':'test-secret-at-least-thirty-two-characters','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_REQUIRE_PAYMENT':'true'});cls.env.start()
  import app_mvp
  cls.web=importlib.reload(app_mvp);cls.web.init_db();cls.web.app.config['TESTING']=True
 @classmethod
 def tearDownClass(cls):cls.env.stop();cls.temp.cleanup()
 def customer(self,name):
  c=self.web.app.test_client();c.get('/account')
  with c.session_transaction() as s:csrf=s['csrf']
  r=c.post('/account',data={'_csrf':csrf,'mode':'register','name':name,'email':name+'@example.test','password':'test-customer-password123'});self.assertEqual(r.status_code,302)
  with c.session_transaction() as s:csrf=s['csrf']
  return c,csrf
 def test_private_pages_need_account(self):
  with self.web.app.test_client() as c:
   for url in ['/studio','/connections','/reports']:self.assertEqual(c.get(url).status_code,401)
 def test_profile_and_export_stay_in_customer_workspace(self):
  a,csrf=self.customer('creative-profile');b,_=self.customer('creative-other')
  self.assertEqual(a.post('/studio/profile',data={'_csrf':csrf,'name':'My unique business','tone':'Clear','location':'My city','hashtags':'local','instructions':'Facts only'}).status_code,302)
  self.assertIn(b'My unique business',a.get('/studio').data);self.assertNotIn(b'My unique business',b.get('/studio').data)
  r=a.post('/studio/export',data={'_csrf':csrf,'content':'My approved caption'});self.assertEqual(r.status_code,200)
  with zipfile.ZipFile(io.BytesIO(r.data)) as z:self.assertEqual(z.read('caption.txt').decode(),'My approved caption')
  self.assertEqual(a.post('/studio/profile',data={'name':'No csrf'}).status_code,403)
 def test_preview_never_calls_live_ai_or_canva(self):
  c,csrf=self.customer('creative-preview')
  with patch('editor_connectors.canva_authorize',side_effect=AssertionError('live OAuth called')):
   self.assertEqual(c.post('/connections/canva/connect',data={'_csrf':csrf}).status_code,302)
  with patch('ai_providers.generate_text',side_effect=AssertionError('paid AI called')):
   self.assertEqual(c.post('/studio/suggest',data={'_csrf':csrf,'task':'Write my post'}).status_code,409)
 def test_customer_pages_render_real_features_without_keys(self):
  c,_=self.customer('creative-screens')
  for url in ['/studio','/connections','/reports']:
   response=c.get(url);self.assertEqual(response.status_code,200,url)
   self.assertNotIn(b'name="api_key"',response.data)
 def test_reschedule_is_scoped_and_keeps_external_post_inactive(self):
  from datetime import datetime,timedelta,timezone
  a,csrf=self.customer('creative-reschedule');b,other_csrf=self.customer('creative-foreign')
  with a.session_transaction() as sess:account_id=sess['account_id']
  import customer_accounts
  ws=customer_accounts.get(account_id)['workspace_id']
  with self.web.db() as db:
   post=db.execute("INSERT INTO app_posts(content,platform,due_at,status,workspace_id) VALUES('Approved facts','draft',?,'setup_only',?)",((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),ws)).lastrowid
  due=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
  self.assertEqual(b.post('/posts/'+str(post)+'/reschedule',data={'_csrf':other_csrf,'due_at':due}).status_code,302)
  self.assertEqual(a.post('/posts/'+str(post)+'/reschedule',data={'_csrf':csrf,'due_at':due}).status_code,302)
  with self.web.db() as db:
   row=db.execute('SELECT status,due_at FROM app_posts WHERE id=?',(post,)).fetchone()
  self.assertEqual(row['status'],'setup_only');self.assertTrue(row['due_at'].startswith(due[:19]))
if __name__=='__main__':unittest.main()
