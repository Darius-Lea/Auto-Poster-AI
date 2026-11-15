"""Exercise the customer-facing bridge without a network post or model download."""
import importlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import clip_branding
import clip_publishing
import clip_studio_web as clips
import customer_accounts
import meta_accounts


class ClipWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.tmp.name + '/app.db',
            'AUTOPOST_CLIP_DATA_DIR': self.tmp.name + '/private-clips',
            'AUTOPOST_APP_PASSWORD': 'synthetic-owner-password123',
            'AUTOPOST_WEB_SECRET': 'synthetic-session-secret-more-than-32-characters',
            'META_PAGES_ENCRYPTION_SECRET': 'synthetic-page-secret-more-than-32-characters',
            'AUTOPOST_REQUIRE_PAYMENT': 'false',
            'AUTOPOST_PREVIEW_MODE': 'false',
            'AUTOPOST_CLIP_ENABLED': 'true',
        })
        environment.start()
        self.addCleanup(environment.stop)
        import app_mvp
        self.web = importlib.reload(app_mvp)
        self.web.init_db()
        self.web.app.config['TESTING'] = True
        ready = patch.object(clips, 'renderer_readiness', return_value={
            'enabled': True, 'ready': True, 'message': 'Fixture renderer ready'})
        ready.start()
        self.addCleanup(ready.stop)
        probe = patch.object(clips, 'probe_source', return_value=120.0)
        probe.start()
        self.addCleanup(probe.stop)
        capabilities = patch('video_sources.capabilities', return_value={
            'direct_video': True, 'message': 'Direct video imports available'})
        capabilities.start()
        self.addCleanup(capabilities.stop)
        self.a, self.csrf, self.user = self.customer('alpha')
        self.b, self.other_csrf, self.other_user = self.customer('beta')
        self.ws = self.user['workspace_id']
        for user, page in ((self.user, '101'), (self.other_user, '202')):
            workspace = user['workspace_id']
            meta_accounts.remember_pending(workspace, [{
                'page_id': page, 'name': 'Fixture business',
                'tasks': ['CREATE_CONTENT'], 'token': 'synthetic-page-token-' + page}])
            meta_accounts.activate_pages(workspace, [page])

    def customer(self, name):
        client = self.web.app.test_client()
        client.get('/account')
        with client.session_transaction() as session:
            csrf = session['csrf']
        response = client.post('/account', data={
            '_csrf': csrf, 'mode': 'register', 'next': 'clips',
            'name': name, 'email': name + '@example.test',
            'password': 'synthetic-customer-password123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/clips')
        with client.session_transaction() as session:
            identifier, csrf = session['account_id'], session['csrf']
        with customer_accounts.connect() as connection:
            connection.execute('UPDATE customer_accounts SET email_verified=1 WHERE id=?', (identifier,))
        return client, csrf, customer_accounts.get(identifier)

    def upload(self):
        response = self.a.post('/clips/jobs', data={
            '_csrf': self.csrf, 'permission_confirmed': 'yes',
            'business_name': 'My Garden', 'accent': '#217A51', 'duration': '30',
            'video': (io.BytesIO(b'synthetic local video'), 'episode.mp4')})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['job']['id']

    def queue_link(self):
        response = self.a.post('/clips/jobs', data={
            '_csrf': self.csrf, 'permission_confirmed': 'yes',
            'business_name': 'My Garden', 'accent': '#217A51', 'duration': '45',
            'video_url': 'https://cdn.example.org/my-video.mp4?signature=private-fixture-marker'})
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(response.json['job']['status'], 'import_queued')
        self.assertNotIn('private-fixture-marker', json.dumps(response.json))
        return response.json['job']['id']

    @staticmethod
    def renderer(source, options, out_dir, **kwargs):
        output = Path(out_dir)
        output.mkdir(parents=True)
        (output / 'clip.mp4').write_bytes(b'synthetic finished video')
        (output / 'clip.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nA useful idea\n')
        (output / 'clip.transcript.json').write_text('[{"w":"A","s":10,"e":10.4}]')
        return {'summary': {'qa_status': 'pass', 'hook': 'A useful idea',
                            'moment': {'start': 10, 'end': 30}}}

    def finish(self):
        with patch('features.clip_studio.render_clip', side_effect=self.renderer) as render:
            self.assertTrue(clips.process_next_job())
        self.assertTrue(render.call_args.kwargs['local_only'])
        return render

    @staticmethod
    def logo():
        output = io.BytesIO()
        Image.new('RGBA', (80, 40), (210, 20, 40, 255)).save(output, format='PNG')
        return output.getvalue()

    def save_logo(self):
        response = self.a.post('/clips/brand', data={
            '_csrf': self.csrf, 'revision': '0', 'name': 'My Garden', 'accent': '#217A51',
            'logo': (io.BytesIO(self.logo()), 'garden.png', 'image/png')})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['profile']

    def schedule(self, identifier):
        values = {'_csrf': self.csrf, 'page_id': '101', 'confirmed': 'yes',
                  'due_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                  'caption': 'An explicitly reviewed video caption.'}
        response = self.a.post('/clips/jobs/' + identifier + '/publish', data=values)
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['job'], values

    def test_pasted_link_is_background_work_and_never_exposes_signed_source(self):
        with patch('video_sources.import_video', side_effect=AssertionError('request downloaded media')):
            identifier = self.queue_link()
        self.assertEqual(self.b.get('/clips/jobs').json['jobs'], [])
        self.assertNotIn('private-fixture-marker', self.a.get('/clips/jobs').get_data(as_text=True))

        def download(url, destination, **kwargs):
            self.assertIn('private-fixture-marker', url)
            Path(destination).write_bytes(b'synthetic downloaded video')
            return {'extension': '.webm', 'bytes': 26, 'duration': 20.0,
                    'mime_type': 'video/webm', 'host': 'cdn.example.org'}

        with patch('video_sources.import_video', side_effect=download) as imported:
            self.assertTrue(clips.process_next_job())
            imported.assert_called_once()
        with clips.connect() as connection:
            row = connection.execute('SELECT * FROM clip_jobs WHERE id=?', (identifier,)).fetchone()
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['source_url'], '')
        self.assertEqual(row['source_ext'], '.webm')
        self.assertEqual(json.loads(row['options_json'])['duration'], 20)
        render = self.finish()
        self.assertTrue(render.call_args.args[0].endswith('/source.webm'))
        self.assertEqual(self.a.get('/clips/jobs').json['jobs'][0]['status'], 'ready')
        self.assertEqual(self.b.get('/clips/jobs/' + identifier + '/download/mp4').status_code, 404)

    def test_failed_link_refunds_only_unrendered_work_and_sanitizes_error(self):
        identifier = self.queue_link()
        with patch('video_sources.import_video', side_effect=ValueError('private-fixture-marker /secret/path')):
            self.assertTrue(clips.process_next_job())
        job = self.a.get('/clips/jobs').json['jobs'][0]
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('private-fixture-marker', json.dumps(job))
        self.assertNotIn('/secret/path', json.dumps(job))
        self.assertFalse(clips.job_dir(identifier).exists())
        with clips.connect() as connection:
            self.assertEqual(connection.execute('SELECT count FROM clip_daily_usage WHERE workspace_id=?',
                                               (self.ws,)).fetchone()[0], 0)

    def test_queued_logo_and_caption_revision_keep_the_original_business_look(self):
        profile = self.save_logo()
        identifier = self.upload()
        original = (clips.job_dir(identifier) / 'brand/logo.png').read_bytes()
        removed = self.a.post('/clips/brand', data={
            '_csrf': self.csrf, 'revision': str(profile['revision']),
            'name': 'Changed later', 'accent': '#0000FF', 'remove_logo': 'yes'})
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.json['profile']['has_logo'])
        self.finish()
        edited = self.a.post('/clips/jobs/' + identifier + '/captions', data={
            '_csrf': self.csrf, 'captions': '1\n00:00:00,000 --> 00:00:01,000\nCorrect words\n'})
        self.assertEqual(edited.status_code, 202, edited.json)
        child = edited.json['job']['id']
        self.assertEqual((clips.job_dir(child) / 'brand/logo.png').read_bytes(), original)
        self.assertEqual(json.loads((clips.job_dir(child) / 'brand/kit.json').read_text())['name'], 'My Garden')
        self.finish()
        self.assertEqual(self.b.get('/clips/brand').json['profile']['revision'], 0)

    def test_reviewed_publish_routes_share_login_enforce_csrf_and_scope_queue(self):
        identifier = self.upload()
        self.finish()
        path = '/clips/jobs/' + identifier + '/publish'
        self.assertEqual(self.a.post(path, data={}).status_code, 403)
        self.assertEqual(self.b.post(path, data={'_csrf': self.other_csrf}).status_code, 404)
        with patch('meta_accounts.publish_video', side_effect=AssertionError('HTTP request posted live video')):
            job, values = self.schedule(identifier)
        self.assertEqual(job['status'], 'scheduled')
        self.assertEqual(self.a.get('/clips/publishing').json['jobs'][0]['id'], job['id'])
        self.assertEqual(self.b.get('/clips/publishing').json['jobs'], [])
        self.assertEqual(self.b.post('/clips/publishing/' + str(job['id']) + '/cancel',
                                    data={'_csrf': self.other_csrf}).status_code, 400)
        denied = self.a.post(path, data={**values, 'confirmed': 'no'})
        self.assertEqual(denied.status_code, 400)
        canceled = self.a.post('/clips/publishing/' + str(job['id']) + '/cancel', data={'_csrf': self.csrf})
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json['job']['status'], 'canceled')

    def test_account_deletion_waits_for_import_and_video_delivery_then_cleans_logo(self):
        import account_support
        identifier = self.queue_link()
        with self.assertRaisesRegex(ValueError, 'clip'):
            account_support.delete_customer(self.user, 'synthetic-customer-password123', 'DELETE')
        self.assertEqual(self.a.post('/clips/jobs/' + identifier + '/delete', data={'_csrf': self.csrf}).status_code, 200)
        self.save_logo()
        identifier = self.upload()
        self.finish()
        job, _ = self.schedule(identifier)
        self.assertEqual(self.a.post('/clips/jobs/' + identifier + '/delete', data={'_csrf': self.csrf}).status_code, 409)
        with self.assertRaisesRegex(ValueError, 'video'):
            account_support.delete_customer(self.user, 'synthetic-customer-password123', 'DELETE')
        self.a.post('/clips/publishing/' + str(job['id']) + '/cancel', data={'_csrf': self.csrf})
        self.assertTrue(account_support.delete_customer(self.user, 'synthetic-customer-password123', 'DELETE'))
        self.assertFalse(clips.job_dir(identifier).exists())
        self.assertFalse(clip_branding._folder(self.ws).exists())
        self.assertEqual(self.a.get('/clips/jobs').status_code, 401)
        self.assertEqual(self.b.get('/clips/jobs').json['jobs'], [])


if __name__ == '__main__':
    unittest.main()
