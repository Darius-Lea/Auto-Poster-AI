"""Install routes remain public while customer content stays authenticated."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': cls.tmp.name + '/app.db',
            'AUTOPOST_APP_PASSWORD': 'synthetic-owner-password123',
            'AUTOPOST_WEB_SECRET': 'synthetic-test-secret-longer-than-thirty-two',
        })
        cls.env.start()
        import app_mvp
        cls.web = importlib.reload(app_mvp)
        cls.web.init_db()
        cls.web.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def test_install_routes_work_without_account(self):
        c = self.web.app.test_client()
        for path in ('/install', '/offline', '/service-worker.js'):
            response = c.get(path)
            self.assertEqual(response.status_code, 200, path)
        worker = c.get('/service-worker.js')
        self.assertEqual(worker.mimetype, 'text/javascript')
        self.assertEqual(worker.headers['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', worker.headers['Cache-Control'])
        self.assertIn(b'Add to Home Screen', c.get('/install').data)

    def test_manifest_uses_real_same_origin_launch_and_icons(self):
        c = self.web.app.test_client()
        manifest = c.get('/manifest.webmanifest').get_json()
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['start_url'], '/launch?source=installed')
        self.assertEqual(c.get(manifest['start_url']).status_code, 200)
        for icon in manifest['icons']:
            with c.get(icon['src']) as response:
                self.assertEqual(response.status_code, 200)

    def test_private_workspace_does_not_become_public(self):
        c = self.web.app.test_client()
        for path in ('/studio', '/studio/draft', '/clips/jobs', '/account/export'):
            self.assertIn(c.get(path).status_code, (401, 405), path)

    def test_offline_document_contains_no_account_content(self):
        c = self.web.app.test_client()
        first = c.get('/offline').data
        with c.session_transaction() as session:
            session.update(authorized=True, csrf='private-session-marker')
        second = c.get('/offline').data
        self.assertEqual(first, second)
        self.assertNotIn(b'private-session-marker', second)


if __name__ == '__main__':
    unittest.main()
