"""Private logo storage, safe pixels, revision control and real renderer kits."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from PIL import Image, PngImagePlugin

import clip_branding as branding


def logo_bytes(size=(80, 40), color=(230, 30, 20, 255), kind='PNG', metadata=False):
    image = Image.new('RGBA', size, color)
    if kind == 'JPEG':
        image = image.convert('RGB')
    output = io.BytesIO()
    options = {}
    if metadata:
        text = PngImagePlugin.PngInfo()
        text.add_text('private-note', 'must not survive normalization')
        options['pnginfo'] = text
    image.save(output, format=kind, **options)
    return output.getvalue()


class ClipBrandingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': str(self.root / 'app.db'),
            'AUTOPOST_CLIP_DATA_DIR': str(self.root / 'private'),
            'AUTOPOST_APP_PASSWORD': 'synthetic-owner-password123',
            'AUTOPOST_WEB_SECRET': 'synthetic-session-secret-more-than-32-characters',
        })
        env.start()
        self.addCleanup(env.stop)
        import customer_accounts
        customer_accounts.init()
        with customer_accounts.connect() as connection:
            # Signed sessions model accounts already authenticated by the app's
            # separately tested login flow. No actual customer credentials.
            for identifier in ('a', 'b'):
                connection.execute('INSERT INTO customer_accounts(id,workspace_id,email,name,password_hash,created_at) VALUES(?,?,?,?,?,0)',
                    (identifier, 'ws_' + identifier, identifier + '@example.test', identifier,
                     'unused synthetic password hash'))
        branding.init()
        self.app = Flask('logo-fixture')
        self.app.config.update(TESTING=True, SECRET_KEY='synthetic-logo-secret-32-characters')
        self.app.register_blueprint(branding.branding_bp)
        self.a = self.app.test_client()
        self.b = self.app.test_client()
        for client, identifier in ((self.a, 'a'), (self.b, 'b')):
            with client.session_transaction() as session:
                session.update(authorized=True, account_id=identifier, auth_version=0,
                               csrf='synthetic-csrf-' + identifier)

    def save(self, client=None, **changes):
        values = {'_csrf': 'synthetic-csrf-a', 'name': 'Mary’s Garden',
                  'accent': '#217A51', 'revision': '0'}
        values.update(changes)
        return (client or self.a).post('/clips/brand', data=values)

    def test_guest_and_csrf_are_rejected_without_mutating_brand(self):
        guest = self.app.test_client()
        self.assertEqual(guest.get('/clips/brand').status_code, 401)
        self.assertEqual(guest.get('/clips/brand/logo').status_code, 401)
        self.assertEqual(guest.post('/clips/brand', data={}).status_code, 401)
        self.assertEqual(self.save(_csrf='wrong').status_code, 403)
        self.assertEqual(self.save(_csrf='wrong-☃').status_code, 403)
        self.assertEqual(branding.get_profile('ws_a')['revision'], 0)

    def test_oversized_request_has_helpful_failure_and_revoked_session_is_private(self):
        response = self.save(logo=(io.BytesIO(b'x' * (branding.MAX_LOGO_BYTES + 65537)),
                                  'oversized.png', 'image/png'))
        self.assertEqual(response.status_code, 413)
        self.assertIn('4 MB', response.json['error'])
        import customer_accounts
        with customer_accounts.connect() as connection:
            connection.execute('UPDATE customer_accounts SET auth_version=auth_version+1 WHERE id=?', ('a',))
        self.assertEqual(self.a.get('/clips/brand').status_code, 401)
        self.assertEqual(self.a.get('/clips/brand/logo').status_code, 401)

    def test_upload_decodes_pixels_strips_metadata_and_is_tenant_scoped(self):
        source = logo_bytes(metadata=True)
        response = self.save(logo=(io.BytesIO(source), 'garden.png', 'image/png'))
        self.assertEqual(response.status_code, 200, response.json)
        profile = response.json['profile']
        self.assertTrue(profile['has_logo'])
        self.assertEqual(profile['revision'], 1)
        self.assertNotIn(self.tmp.name, json.dumps(response.json))
        self.assertFalse(self.b.get('/clips/brand').json['profile']['has_logo'])
        self.assertEqual(self.b.get(profile['logo_url']).status_code, 404)
        with self.a.get(profile['logo_url']) as saved:
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.headers['Cache-Control'], 'no-store')
            self.assertEqual(saved.mimetype, 'image/png')
            with Image.open(io.BytesIO(saved.data)) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.size, (80, 80))
                self.assertEqual(image.getpixel((40, 2))[3], 0)
                self.assertEqual(image.getpixel((40, 40))[:3], (230, 30, 20))
                self.assertNotIn('private-note', image.info)

    def test_jpeg_webp_and_transparency_normalize_without_stretching(self):
        for kind, extension, mime in (('JPEG', 'jpg', 'image/jpeg'), ('WEBP', 'webp', 'image/webp')):
            source = logo_bytes(kind=kind)
            data = branding.normalize_logo(source, 'my-logo.' + extension, mime)
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.size, (80, 80))
                self.assertEqual(image.getpixel((0, 0))[3], 0)
        data = branding.normalize_logo(logo_bytes(color=(20, 30, 40, 80)), 'a.png', 'image/png')
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.getpixel((40, 40))[3], 80)

    def test_active_svg_fake_mime_corrupt_and_animation_are_rejected(self):
        for data, filename, mime in (
            (b'<svg onload="alert(1)"></svg>', 'x.svg', 'image/svg+xml'),
            (logo_bytes(), 'x.html', 'image/png'),
            (logo_bytes(), 'x.jpg', 'image/jpeg'),
            (b'not a PNG', 'x.png', 'image/png'),
            (logo_bytes(), 'x.png', 'image/svg+xml'),
        ):
            with self.assertRaises(ValueError):
                branding.normalize_logo(data, filename, mime)
        output = io.BytesIO()
        one = Image.new('RGBA', (40, 40), 'red')
        two = Image.new('RGBA', (40, 40), 'blue')
        one.save(output, format='PNG', save_all=True, append_images=[two], duration=100)
        with self.assertRaisesRegex(ValueError, 'still image'):
            branding.normalize_logo(output.getvalue(), 'motion.png', 'image/png')

    def test_size_pixel_and_dimension_limits_apply_before_pixel_decoding(self):
        with self.assertRaises(ValueError):
            branding.normalize_logo(b'x' * (branding.MAX_LOGO_BYTES + 1), 'x.png', 'image/png')
        for size in ((8, 8), (8193, 16), (4001, 4000)):
            with self.assertRaises(ValueError):
                branding.normalize_logo(logo_bytes(size=size), 'x.png', 'image/png')
        with patch.object(Image, 'MAX_IMAGE_PIXELS', 100):
            with self.assertRaisesRegex(ValueError, 'too large'):
                branding.normalize_logo(logo_bytes(size=(80, 40)), 'x.png', 'image/png')
        data = branding.normalize_logo(logo_bytes(size=(3000, 1000)), 'wide.png', 'image/png')
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (2048, 2048))

    def test_stale_revision_and_remove_upload_conflict_do_not_change_brand(self):
        self.assertEqual(self.save().status_code, 200)
        stale = self.save(name='stale overwrite')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json['profile']['name'], 'Mary’s Garden')
        conflict = self.save(revision='1', remove_logo='yes',
            logo=(io.BytesIO(logo_bytes()), 'new.png', 'image/png'))
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(branding.get_profile('ws_a')['revision'], 1)
        for changes in ({'name':'bad\nname'}, {'accent':'url(https://example.test)'}, {'revision':'-1'}):
            self.assertEqual(self.save(**changes).status_code, 400)

    def test_queued_snapshot_keeps_logo_and_color_after_replacement_removal(self):
        original = branding.normalize_logo(logo_bytes(), 'red.png', 'image/png')
        branding.save_profile('ws_a', name='Garden', accent='#217A51', revision=0, logo=original)
        job = self.root / 'job'
        kit_path = branding.snapshot_brand('ws_a', job)
        kit = json.loads(kit_path.read_text())
        self.assertFalse(kit['text_only'])
        self.assertEqual(kit['logo'], 'logo.png')
        self.assertEqual((kit_path.parent / kit['logo']).read_bytes(), original)
        branding.save_profile('ws_a', name='New Look', accent='#0000FF', revision=1,
            logo=branding.normalize_logo(logo_bytes(color=(0, 0, 255, 255)), 'blue.png', 'image/png'))
        branding.save_profile('ws_a', name='No Logo', accent='#000000', revision=2, remove_logo=True)
        self.assertFalse(branding.get_profile('ws_a')['has_logo'])
        self.assertEqual((job / 'brand/logo.png').read_bytes(), original)
        self.assertEqual(json.loads(kit_path.read_text())['accent'], '#217A51')
        self.assertEqual(self.a.get('/clips/brand/logo').status_code, 404)
        child = self.root / 'another-job'
        current = json.loads(branding.snapshot_brand('ws_a', child).read_text())
        self.assertTrue(current['text_only'])

    def test_customer_logo_is_used_by_actual_renderer_card(self):
        original = branding.normalize_logo(logo_bytes(size=(80, 40)), 'red.png', 'image/png')
        branding.save_profile('ws_a', name='Garden', accent='#217A51', revision=0, logo=original)
        path = branding.snapshot_brand('ws_a', self.root / 'job')
        from features.clip_studio.engine import clip_engine as engine
        kit = engine.load_brand(str(path))
        with patch.object(engine, 'run'):
            engine.render_card(str(self.root), kit, style='standard')
        with Image.open(self.root / 'card.png') as card:
            self.assertEqual(card.size, (1080, 1920))
            self.assertEqual(card.getpixel((540, 900))[:3], (230, 30, 20))
            self.assertEqual(card.getpixel((540, 450))[:3], (12, 20, 37))

    def test_deleted_account_cannot_save_or_snapshot_and_cleanup_is_scoped(self):
        original = branding.normalize_logo(logo_bytes(), 'a.png', 'image/png')
        for ws in ('ws_a', 'ws_b'):
            branding.save_profile(ws, name='Business', accent='#123456', revision=0, logo=original)
        branding.delete_workspace('ws_a')
        self.assertFalse(branding._folder('ws_a').exists())
        self.assertTrue(branding._folder('ws_b').exists())
        import customer_accounts
        with customer_accounts.connect() as connection:
            connection.execute('DELETE FROM customer_accounts WHERE workspace_id=?', ('ws_a',))
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            branding.save_profile('ws_a', name='Late write', accent='#123456', revision=0)
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            branding.snapshot_brand('ws_a', self.root / 'late-job')

    def test_symlink_blob_cannot_be_served_or_snapshotted(self):
        data = branding.normalize_logo(logo_bytes(), 'a.png', 'image/png')
        branding.save_profile('ws_a', name='Business', accent='#123456', revision=0, logo=data)
        with branding.connect() as connection:
            digest = connection.execute('SELECT logo_digest FROM clip_brand_profiles WHERE workspace_id=?', ('ws_a',)).fetchone()[0]
        blob = branding._folder('ws_a') / ('logo-' + digest + '.png')
        blob.unlink()
        outside = self.root / 'do-not-expose.png'
        outside.write_bytes(b'private file')
        blob.symlink_to(outside)
        self.assertEqual(self.a.get('/clips/brand/logo').status_code, 404)
        with self.assertRaises(OSError):
            branding.snapshot_brand('ws_a', self.root / 'job')


if __name__ == '__main__':
    unittest.main()
