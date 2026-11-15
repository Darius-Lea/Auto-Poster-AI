"""Tenant video delivery, official API contract and shared text/video quotas."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app_control
import clip_publishing as videos
import meta_accounts
import plan_limits


class ClipPublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name+'/app.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name+'/clips',
            'AUTOPOST_APP_PASSWORD':'synthetic-owner-password123',
            'AUTOPOST_WEB_SECRET':'synthetic-test-web-secret-longer-than-32',
            'META_PAGES_ENCRYPTION_SECRET':'synthetic-page-encryption-secret-longer-than32',
            'AUTOPOST_REQUIRE_PAYMENT':'false','AUTOPOST_PREVIEW_MODE':'false',
        }); self.env.start(); self.addCleanup(self.env.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp); self.web.init_db(); videos.init()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        import customer_accounts
        self.alpha = customer_accounts.register('video-alpha@example.test','synthetic-password123','Alpha')
        self.beta = customer_accounts.register('video-beta@example.test','synthetic-password123','Beta')
        self.ws = self.alpha['workspace_id']; self.other = self.beta['workspace_id']
        with customer_accounts.connect() as c:
            c.execute('UPDATE customer_accounts SET email_verified=1')
        for workspace,page in ((self.ws,'101'),(self.other,'202')):
            meta_accounts.remember_pending(workspace,[{'page_id':page,'name':'Fixture Page','tasks':['CREATE_CONTENT'],'token':'synthetic-page-token-'+page}])
            meta_accounts.activate_pages(workspace,[page])
        self.clip = self.make_clip(self.ws, 'a'*32)
        self.other_clip = self.make_clip(self.other, 'b'*32)

    def make_clip(self, workspace, identifier):
        import clip_studio_web
        with videos.connect() as c:
            c.execute('INSERT INTO clip_jobs(id,workspace_id,status,created,updated,source_ext,source_duration,options_json,summary_json) VALUES(?,?,?,?,?,?,?,?,?)',
                      (identifier,workspace,'ready',int(self.now.timestamp()),int(self.now.timestamp()),'.mp4',60,'{}',json.dumps({'qa_status':'pass'})))
        folder = clip_studio_web.job_dir(identifier)/'output'; folder.mkdir(parents=True)
        (folder/'clip.mp4').write_bytes(b'finished synthetic MP4 fixture')
        return identifier

    def schedule(self, **values):
        return videos.schedule(values.pop('workspace',self.ws), values.pop('clip_id',self.clip),values.pop('page_id','101'),
            values.pop('due_at',self.now.isoformat()),values.pop('caption','An approved video caption.'),
            permission_confirmed=values.pop('permission_confirmed',True),now=self.now,**values)

    def text_post(self, identifier=100):
        with videos.connect() as c:
            c.execute("INSERT INTO app_posts(id,workspace_id,content,platform,due_at,status,page_id) VALUES(?,?,?,'facebook_page',?,'scheduled','101')",
                      (identifier,self.ws,'A reviewed text post',self.now.isoformat()))

    def test_queue_requires_rights_verified_identity_page_payment_pause_and_real_ready_clip(self):
        for changes in ({'permission_confirmed':False},{'page_id':'202'},{'clip_id':self.other_clip},{'caption':''}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,PermissionError)):
                self.schedule(**changes)
        with patch.dict(os.environ,{'AUTOPOST_PREVIEW_MODE':'true'}),self.assertRaises(PermissionError):self.schedule()
        app_control.set_publishing_paused(self.ws,True)
        with self.assertRaises(PermissionError):self.schedule()
        app_control.set_publishing_paused(self.ws,False)
        with patch('app_control.allowed',return_value=False),self.assertRaises(PermissionError):self.schedule()
        with videos.connect() as c:c.execute('UPDATE customer_accounts SET email_verified=0 WHERE workspace_id=?',(self.ws,))
        with self.assertRaises(PermissionError):self.schedule()

    def test_schedule_is_tenant_scoped_and_idempotent_without_network(self):
        with patch('meta_accounts.requests.post',side_effect=AssertionError('schedule posted')):
            first=self.schedule(); second=self.schedule()
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['status'],'scheduled')
        self.assertEqual(videos.jobs(self.other),[])
        self.assertNotIn(self.tmp.name,json.dumps(first));self.assertNotIn('token',json.dumps(first))
        with self.assertRaises(ValueError):self.schedule(caption='Silently changed caption')
        with self.assertRaises(ValueError):videos.cancel(self.other,first['id'])
        self.assertTrue(videos.workspace_busy(self.ws))
        self.assertEqual(videos.cancel(self.ws,first['id'])['status'],'canceled')
        self.assertFalse(videos.workspace_busy(self.ws))

    def test_published_is_confirmed_only_after_processing_get_and_upload_is_not_repeated(self):
        self.schedule()
        with patch('meta_accounts.publish_video',return_value='555') as upload:
            self.assertTrue(videos.process_next_publish(self.now))
            self.assertEqual(videos.jobs(self.ws)[0]['status'],'processing')
            self.assertFalse(videos.process_next_publish(self.now+timedelta(seconds=10)))
            with patch('meta_accounts.get_video_status',return_value={'published':False,'failed':False,'processing':True,'permalink_url':None}):
                self.assertTrue(videos.process_next_publish(self.now+timedelta(seconds=61)))
            self.assertEqual(videos.jobs(self.ws)[0]['status'],'processing')
            with patch('meta_accounts.get_video_status',return_value={'published':True,'failed':False,'permalink_url':'https://www.facebook.com/watch/?v=555'}):
                self.assertTrue(videos.process_next_publish(self.now+timedelta(seconds=122)))
            upload.assert_called_once()
        result=videos.jobs(self.ws)[0];self.assertEqual(result['status'],'published');self.assertEqual(result['remote_id'],'555')

    def test_uncertain_post_is_held_never_retried_and_keeps_daily_allowance(self):
        self.schedule()
        with patch('meta_accounts.publish_video',side_effect=RuntimeError('/private/path Bearer fake-secret')) as upload:
            self.assertTrue(videos.process_next_publish(self.now))
            self.assertFalse(videos.process_next_publish(self.now+timedelta(hours=1)))
            upload.assert_called_once()
        result=videos.jobs(self.ws)[0]
        self.assertEqual(result['status'],'needs_review');self.assertNotIn('private',result['error']);self.assertNotIn('secret',result['error'])
        self.text_post()
        with self.assertRaises(plan_limits.PlanLimitError):plan_limits.reserve_delivery(self.ws,100,'facebook_page','101',self.now)
        with self.assertRaises(ValueError):videos.cancel(self.ws,result['id'])

    def test_pause_and_subscription_changes_before_send_leave_scheduled_without_consuming_quota(self):
        self.schedule();app_control.set_publishing_paused(self.ws,True)
        with patch('meta_accounts.publish_video',side_effect=AssertionError('paused upload')):
            self.assertFalse(videos.process_next_publish(self.now))
        app_control.set_publishing_paused(self.ws,False)
        with patch('app_control.allowed',return_value=False),patch('meta_accounts.publish_video',side_effect=AssertionError('unpaid upload')):
            self.assertFalse(videos.process_next_publish(self.now))
        self.assertEqual(videos.jobs(self.ws)[0]['status'],'scheduled')
        with videos.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM plan_delivery_reservations').fetchone()[0],0)

    def test_text_and_video_schedules_share_the_basic_day_and_exclusions(self):
        queued=self.schedule()
        with self.assertRaises(plan_limits.PlanLimitError):plan_limits.check_schedule(self.ws,[('facebook_page',None,'101')],self.now)
        self.assertTrue(plan_limits.check_schedule(self.ws,[('facebook_page',None,'101')],self.now,exclude_clip_ids=[queued['id']]))
        videos.cancel(self.ws,queued['id']);self.text_post()
        with self.assertRaises(plan_limits.PlanLimitError):self.schedule()

    def test_atomic_text_video_reservations_cannot_take_same_allowance(self):
        queued=self.schedule();self.text_post()
        barrier=threading.Barrier(2)
        def reserve(video):
            barrier.wait(timeout=5)
            try:
                if video:plan_limits.reserve_clip_delivery(self.ws,queued['id'],'101',now=self.now)
                else:plan_limits.reserve_delivery(self.ws,100,'facebook_page','101',now=self.now)
                return True
            except plan_limits.PlanLimitError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(reserve,[True,False])),[False,True])
        with videos.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM plan_delivery_reservations').fetchone()[0],1)

    def test_recovery_holds_interrupted_post_without_reupload_but_processing_can_be_polled(self):
        queued=self.schedule()
        with videos.connect() as c:c.execute("UPDATE clip_publish_jobs SET status='sending' WHERE id=?",(queued['id'],))
        videos.recover_interrupted()
        self.assertEqual(videos.jobs(self.ws)[0]['status'],'needs_review')
        with patch('meta_accounts.publish_video',side_effect=AssertionError('recovery reuploaded')):
            self.assertFalse(videos.process_next_publish(self.now))

    def test_official_upload_is_streamed_uses_only_scoped_token_and_checks_processing(self):
        import clip_studio_web
        path=clip_studio_web.job_dir(self.clip)/'output'/'clip.mp4'
        calls=[]
        def post(url,**kwargs):
            body=b''.join(kwargs['data'])
            calls.append((url,kwargs,body))
            return type('Response',(),{'status_code':200,'json':lambda self:{'id':'555'}})()
        with patch('meta_accounts.requests.post',side_effect=post):
            self.assertEqual(meta_accounts.publish_video(self.ws,'101',path,'A clean caption'),'555')
        url,kwargs,body=calls[0]
        self.assertEqual(url,'https://graph-video.facebook.com/v26.0/101/videos')
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer synthetic-page-token-101')
        self.assertFalse(kwargs['allow_redirects']);self.assertEqual(kwargs['timeout'],(10,120))
        self.assertIn(b'name="source"; filename="clip.mp4"',body);self.assertIn(b'A clean caption',body)
        self.assertEqual(int(len(body)),len(kwargs['data']))
        with patch('meta_accounts._get_json',return_value={'id':'555','published':True,'status':{'video_status':'processing'}}):
            self.assertFalse(meta_accounts.get_video_status(self.ws,'101','555')['published'])
        with patch('meta_accounts._get_json',return_value={'id':'555','published':True,'status':{'video_status':'ready'}}):
            self.assertTrue(meta_accounts.get_video_status(self.ws,'101','555')['published'])
        with patch('meta_accounts._get_json',return_value={'id':'555','published':False,'status':{'video_status':'error'}}):
            self.assertTrue(meta_accounts.get_video_status(self.ws,'101','555')['failed'])

    def test_wrong_page_token_and_changed_or_missing_clip_never_start_upload(self):
        queued=self.schedule()
        with videos.connect() as c:c.execute("UPDATE clip_jobs SET status='failed' WHERE id=?",(self.clip,))
        with patch('meta_accounts.publish_video',side_effect=AssertionError('invalid clip uploaded')):
            self.assertTrue(videos.process_next_publish(self.now))
        self.assertEqual(videos.jobs(self.ws)[0]['status'],'needs_review')
        import clip_studio_web
        valid_path=clip_studio_web.job_dir(self.other_clip)/'output'/'clip.mp4'
        with self.assertRaises(PermissionError):meta_accounts.publish_video(self.other,'101',valid_path,'No')

    def test_status_errors_processing_deadline_and_token_failures_are_held_without_reupload(self):
        self.schedule()
        with patch('meta_accounts.publish_video',return_value='555'):
            videos.process_next_publish(self.now)
        with patch('meta_accounts.get_video_status',side_effect=RuntimeError('Bearer private-token')):
            self.assertTrue(videos.process_next_publish(self.now+timedelta(seconds=61)))
        job=videos.jobs(self.ws)[0]
        self.assertEqual(job['status'],'processing');self.assertNotIn('token',job['error'])
        with videos.connect() as c:
            c.execute("UPDATE clip_publish_jobs SET status='processing',poll_after=0,updated=? WHERE id=?",(int(self.now.timestamp()),job['id']))
        with patch('meta_accounts.get_video_status',return_value={'published':False,'failed':False}),patch('meta_accounts.publish_video',side_effect=AssertionError('poll POSTed')):
            videos.process_next_publish(self.now+timedelta(days=2))
        self.assertEqual(videos.jobs(self.ws)[0]['status'],'needs_review')

    def test_processing_poll_does_not_starve_another_customers_scheduled_upload(self):
        self.schedule()
        with patch('meta_accounts.publish_video',side_effect=['555','666']) as upload:
            self.assertTrue(videos.process_next_publish(self.now))
            self.schedule(workspace=self.other,clip_id=self.other_clip,page_id='202')
            with patch('meta_accounts.get_video_status',return_value={'published':False,'failed':False}):
                self.assertTrue(videos.process_next_publish(self.now+timedelta(seconds=61)))
            self.assertEqual(upload.call_count,2)
        self.assertEqual(videos.jobs(self.other)[0]['status'],'processing')

    def test_provider_errors_and_malformed_upload_references_do_not_leak_tokens(self):
        import clip_studio_web
        path=clip_studio_web.job_dir(self.clip)/'output'/'clip.mp4'
        bad=type('Response',(),{'status_code':200,'json':lambda self:{'id':'https://private.example/token'}})()
        with patch('meta_accounts.requests.post',return_value=bad),self.assertRaisesRegex(RuntimeError,'valid video reference'):
            meta_accounts.publish_video(self.ws,'101',path,'Caption')
        with patch('meta_accounts.requests.post',side_effect=meta_accounts.requests.Timeout('secret-token')):
            with self.assertRaises(RuntimeError) as result:meta_accounts.publish_video(self.ws,'101',path,'Caption')
            self.assertNotIn('secret-token',str(result.exception))
        with patch('meta_accounts._get_json',return_value={'id':'999','published':True,'status':{'video_status':'ready'}}),self.assertRaises(RuntimeError):
            meta_accounts.get_video_status(self.ws,'101','555')


if __name__=='__main__':unittest.main()
