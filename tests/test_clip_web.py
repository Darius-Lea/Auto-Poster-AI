"""Offline customer upload, worker and private-artifact integration checks."""
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import clip_studio_web as clips


class ClipWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/app.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/private-clips',
            'AUTOPOST_APP_PASSWORD': 'synthetic-owner-password123',
            'AUTOPOST_WEB_SECRET': 'synthetic-session-secret-more-than-32-characters',
            'AUTOPOST_REQUIRE_PAYMENT': 'false', 'AUTOPOST_PREVIEW_MODE': 'false',
            'AUTOPOST_CLIP_ENABLED': 'true'})
        env.start(); self.addCleanup(env.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.init_db(); clips.init()
        self.web.app.config['TESTING'] = True
        self.ready = patch.object(clips, 'renderer_readiness', return_value={'enabled': True, 'ready': True, 'message': 'Ready'})
        self.ready.start(); self.addCleanup(self.ready.stop)
        self.probe = patch.object(clips, 'probe_source', return_value=120.0)
        self.probe.start(); self.addCleanup(self.probe.stop)
        self.a, self.csrf, self.ws = self.customer('first')
        self.b, self.other_csrf, self.other_ws = self.customer('second')

    def customer(self, name):
        c = self.web.app.test_client(); c.get('/account')
        with c.session_transaction() as s: csrf = s['csrf']
        r = c.post('/account', data={'_csrf': csrf, 'mode': 'register', 'name': name,
            'email': name+'@example.test', 'password': 'synthetic-customer-password123'})
        self.assertEqual(r.status_code, 302)
        with c.session_transaction() as s:
            csrf, identifier = s['csrf'], s['account_id']
        import customer_accounts
        user = customer_accounts.get(identifier)
        with customer_accounts.connect() as connection:
            connection.execute('UPDATE customer_accounts SET email_verified=1 WHERE id=?', (identifier,))
        return c, csrf, user['workspace_id']

    def upload(self, client=None, csrf=None, **extra):
        return (client or self.a).post('/clips/jobs', data={
            '_csrf': csrf or self.csrf, 'video': (io.BytesIO(b'fixture-video'), 'episode.mp4'),
            'permission_confirmed': 'yes', 'duration': '30', 'business_name': 'My Bakery',
            'accent': '#AABBCC', **extra})

    @staticmethod
    def render(source, options, out_dir, **kwargs):
        folder = Path(out_dir); folder.mkdir(parents=True)
        (folder/'clip.mp4').write_bytes(b'fixture-finished-video')
        (folder/'clip.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nHello there\n')
        (folder/'clip.transcript.json').write_text('[{"w":"Hello","s":10,"e":11}]')
        return {'summary': {'qa_status': 'pass', 'hook': 'My real hook', 'moment': {'start':10,'end':40}},
                'clip': str(folder/'clip.mp4')}

    def finish(self):
        with patch('features.clip_studio.render_clip', side_effect=self.render) as call:
            self.assertTrue(clips.process_next_job())
            self.assertTrue(call.call_args.kwargs['local_only'])
            self.assertEqual(call.call_args.kwargs['timeout'], 1800)
            return call

    def test_auth_csrf_email_preview_and_disabled_guards(self):
        anonymous = self.web.app.test_client()
        self.assertEqual(anonymous.get('/clips/jobs').status_code, 401)
        self.assertEqual(self.a.post('/clips/jobs', data={}).status_code, 403)
        import customer_accounts
        with customer_accounts.connect() as c:
            c.execute('UPDATE customer_accounts SET email_verified=0 WHERE workspace_id=?', (self.ws,))
        self.assertEqual(self.upload().status_code, 403)
        with customer_accounts.connect() as c:
            c.execute('UPDATE customer_accounts SET email_verified=1 WHERE workspace_id=?', (self.ws,))
        with patch.dict(os.environ, {'AUTOPOST_PREVIEW_MODE':'true'}):
            self.assertEqual(self.upload().status_code, 409)
        with patch.object(clips, 'renderer_readiness', return_value={'ready':False,'message':'Service setup pending'}):
            self.assertEqual(self.upload().status_code, 503)

    def test_upload_is_async_scoped_and_rights_bound(self):
        self.assertEqual(self.upload(permission_confirmed='no').status_code, 400)
        self.assertEqual(self.upload(url='https://example.test/video.mp4').status_code, 400)
        with patch('features.clip_studio.render_clip', side_effect=AssertionError('request rendered')):
            response = self.upload()
        self.assertEqual(response.status_code, 202)
        job = response.json['job']; self.assertEqual(job['status'], 'queued')
        self.assertEqual(self.b.get('/clips/jobs').json['jobs'], [])
        for suffix in ['/download/mp4', '/download/srt', '/download/json']:
            self.assertEqual(self.b.get('/clips/jobs/'+job['id']+suffix).status_code, 404)
        self.assertNotIn(self.tmp.name, json.dumps(response.json))

    def test_validation_source_duration_extension_and_bounded_stream(self):
        self.assertEqual(self.upload(video=(io.BytesIO(b'x'), 'script.html')).status_code, 400)
        self.assertEqual(self.upload(duration='NaN').status_code, 400)
        self.assertEqual(self.upload(duration='90').status_code, 400)
        self.assertEqual(self.upload(accent='url(http://example.test)').status_code, 400)
        self.assertEqual(self.upload(mode='manual', start='110', duration='30').status_code, 400)
        with patch.object(clips, 'upload_limit', return_value=2):
            self.assertEqual(self.upload().status_code, 400)

    def test_invalid_uploads_refund_allowance_and_short_video_auto_fits(self):
        with patch.object(clips, 'probe_source', side_effect=ValueError('Choose a readable video.')):
            for _ in range(3):
                self.assertEqual(self.upload().status_code, 400)
        with patch.object(clips, 'probe_source', return_value=20):
            response = self.upload(duration='45')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['job']['options']['duration'],20)
        with clips.connect() as c:
            usage = c.execute('SELECT count FROM clip_daily_usage WHERE workspace_id=?',(self.ws,)).fetchone()
        self.assertEqual(usage['count'],1)

    def test_queue_and_daily_limits_cannot_be_reset_by_delete(self):
        first = self.upload().json['job']['id']
        second = self.upload().json['job']['id']
        self.assertEqual(self.upload().status_code, 429)
        for identifier in (first, second):
            self.assertEqual(self.a.post('/clips/jobs/'+identifier+'/delete', data={'_csrf':self.csrf}).status_code, 200)
        third = self.upload().json['job']['id']
        self.assertEqual(self.a.post('/clips/jobs/'+third+'/delete', data={'_csrf':self.csrf}).status_code, 200)
        self.assertEqual(self.upload().status_code, 429)

    def test_worker_success_private_download_and_brand(self):
        identifier = self.upload().json['job']['id']; call = self.finish()
        options = call.call_args.args[1]
        kit = json.loads(Path(options['brand']).read_text())
        self.assertEqual(kit['name'], 'My Bakery'); self.assertTrue(kit['text_only'])
        self.assertEqual(kit['accent'], '#AABBCC')
        jobs = self.a.get('/clips/jobs').json['jobs']; self.assertEqual(jobs[0]['status'], 'ready')
        self.assertNotIn('brand', jobs[0]['options'])
        for kind in ('mp4','srt','json'):
            response = self.a.get('/clips/jobs/'+identifier+'/download/'+kind)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            response.close()
        summary = self.a.get('/clips/jobs/'+identifier+'/download/json')
        self.assertNotIn(self.tmp.name, summary.get_data(as_text=True)); summary.close()
        self.assertFalse(clips.process_next_job())

    def test_failure_does_not_expose_engine_paths_or_download(self):
        identifier = self.upload().json['job']['id']
        with patch('features.clip_studio.render_clip', side_effect=RuntimeError('/private/secret/customer.mp4')):
            self.assertTrue(clips.process_next_job())
        job = self.a.get('/clips/jobs').json['jobs'][0]
        self.assertEqual(job['status'], 'failed'); self.assertNotIn('/private', job['error'])
        self.assertEqual(self.a.get('/clips/jobs/'+identifier+'/download/mp4').status_code, 404)

    def test_captions_reuse_transcript_window_and_ownership(self):
        identifier = self.upload().json['job']['id']; self.finish()
        values = {'_csrf':self.csrf,'captions':'1\n00:00:00,000 --> 00:00:01,000\nCorrect name\n'}
        self.assertEqual(self.b.post('/clips/jobs/'+identifier+'/captions', data={**values,'_csrf':self.other_csrf}).status_code, 404)
        response = self.a.post('/clips/jobs/'+identifier+'/captions', data=values)
        self.assertEqual(response.status_code, 202)
        call = self.finish(); options = call.call_args.args[1]
        self.assertEqual(options['start'], 10); self.assertEqual(options['end'],40)
        self.assertTrue(Path(options['transcript_file']).is_file())
        self.assertIn('Correct name', Path(options['captions_file']).read_text())

    def test_interrupted_work_is_held_and_account_delete_guarded(self):
        identifier = self.upload().json['job']['id']
        self.assertTrue(clips.workspace_busy(self.ws))
        with self.assertRaises(ValueError): clips.delete_workspace(self.ws)
        with clips.connect() as c: c.execute("UPDATE clip_jobs SET status='rendering' WHERE id=?", (identifier,))
        clips.recover_interrupted()
        self.assertFalse(clips.workspace_busy(self.ws))
        clips.delete_workspace(self.ws)
        self.assertFalse(clips.job_dir(identifier).exists())

    def test_account_deletion_removes_ready_video_and_blocks_pending(self):
        identifier = self.upload().json['job']['id']
        import customer_accounts, account_support
        with self.a.session_transaction() as s: user = customer_accounts.get(s['account_id'])
        with self.assertRaisesRegex(ValueError, 'clip'):
            account_support.delete_customer(user, 'synthetic-customer-password123', 'DELETE')
        self.finish()
        self.assertTrue(clips.job_dir(identifier).is_dir())
        self.assertTrue(account_support.delete_customer(user, 'synthetic-customer-password123', 'DELETE'))
        self.assertFalse(clips.job_dir(identifier).exists())
        self.assertEqual(self.b.get('/clips/jobs').json['jobs'], [])

    def test_global_queue_single_worker_and_deleted_customer_guard(self):
        identifier = self.upload().json['job']['id']
        with clips.connect() as c:
            c.execute("UPDATE clip_jobs SET status='rendering' WHERE id=?", (identifier,))
        with patch('features.clip_studio.render_clip', side_effect=AssertionError('second worker rendered')):
            self.assertFalse(clips.process_next_job())
        with clips.connect() as c:
            c.execute('DELETE FROM customer_accounts WHERE workspace_id=?', (self.ws,))
        with self.assertRaisesRegex(PermissionError, 'confirmed account'):
            clips._reserve(self.ws, '.mp4', {'duration':30})

    def test_probe_rejects_network_protocols_and_nonvideo(self):
        self.probe.stop()
        completed = type('Completed', (), {'returncode':0, 'stdout':json.dumps({'format':{'duration':'120'}, 'streams':[{'codec_type':'video'},{'codec_type':'audio'}]})})()
        with patch.object(clips.subprocess, 'run', return_value=completed) as command:
            self.assertEqual(clips.probe_source(Path(self.tmp.name)/'video.mp4'),120)
            self.assertIn('file,pipe', command.call_args.args[0])
            self.assertIn('-format_whitelist', command.call_args.args[0])
            self.assertIn('mov,mp4,m4a,3gp,3g2,mj2,matroska,webm', command.call_args.args[0])
            self.assertEqual(command.call_args.kwargs['timeout'],15)
        completed.stdout = json.dumps({'format':{'duration':'NaN'},'streams':[]})
        with patch.object(clips.subprocess, 'run', return_value=completed):
            with self.assertRaises(ValueError): clips.probe_source(Path(self.tmp.name)/'x.mp4')


if __name__ == '__main__': unittest.main()
