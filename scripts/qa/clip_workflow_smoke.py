"""Phone/desktop customer Clip Studio workflow against synthetic service fixtures.

Exercises real accounts, private logo storage/snapshots, clip queue, captions,
reviewed scheduling and login persistence. Decoder/model and provider transports
are replaced ONLY in the temporary test server. This is not live transcription,
YouTube import, Facebook publication or production-email verification evidence.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from unittest.mock import patch

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = 'synthetic-workflow-customer-password123'

SERVER = r'''
import json, os, shutil, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import app_mvp, app_control, account_support, clip_studio_web as clips, clip_publishing, meta_accounts, video_sources
import features.clip_studio
from werkzeug.serving import make_server

app_mvp.init_db()
account_support.mail_configured=lambda: True
app_control.allowed=lambda *args,**kwargs: True
clips.renderer_readiness=lambda: {'enabled':True,'ready':True,'message':'Ready in this temporary fixture.'}
clips.probe_source=lambda path: 120.0
video_sources.capabilities=lambda: {'direct_video':True,'youtube':False,'formats':['mp4','webm'],'watch_pages':False,'message':'Direct public video file links in this fixture.'}

def no_network(*args,**kwargs):
    raise AssertionError('A provider HTTP request escaped its test fixture')
requests.sessions.Session.request=no_network

sample=Path('features/clip_studio/app/assets/example-clip.mp4').resolve()
def render(source,options,out_dir,**kwargs):
    assert kwargs['local_only'] is True
    output=Path(out_dir);output.mkdir(parents=True)
    shutil.copyfile(sample,output/'clip.mp4')
    words='Hello garden'
    if options.get('captions_file'):
        source_captions=Path(options['captions_file']).read_text()
        words=source_captions.strip().splitlines()[-1]
    (output/'clip.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n'+words+'\n')
    (output/'clip.transcript.json').write_text('[{"w":"Hello","s":10,"e":11}]')
    kit=json.loads(Path(options['brand']).read_text())
    return {'clip':str(output/'clip.mp4'),'summary':{'qa_status':'pass','hook':'One useful garden idea','brand':kit['name'],'moment':{'start':10,'end':40}}}
features.clip_studio.render_clip=render

def import_video(url,destination,**kwargs):
    shutil.copyfile(sample,destination)
    return {'extension':'.mp4','bytes':Path(destination).stat().st_size,'duration':120.0,'mime_type':'video/mp4','host':'media.example.org'}
video_sources.import_video=import_video
video_sources.import_video_dispatch=import_video

call_file=Path(os.environ['AUTOPOST_QA_CALL_FILE'])
def upload(workspace,page_id,path,caption):
    assert Path(path).is_file()
    with call_file.open('a') as handle:handle.write(workspace+'\n')
    return '555123'
meta_accounts.publish_video=upload
meta_accounts.get_video_status=lambda *args,**kwargs: {'published':True,'failed':False,'processing':False,'permalink_url':'https://www.facebook.com/watch/?v=555123'}

def fixture_worker():
    # Synthetic time advances only for read-only publication status polling.
    while True:
        try:
            clips.process_next_job()
            now=datetime.now(timezone.utc)
            clip_publishing.process_next_publish(now)
            clip_publishing.process_next_publish(now+timedelta(seconds=61))
        except Exception:
            import traceback;traceback.print_exc()
        time.sleep(.15)
threading.Thread(target=fixture_worker,daemon=True).start()
server=make_server('127.0.0.1',0,app_mvp.app,threaded=True)
print(server.server_port,flush=True)
server.serve_forever()
'''


def no_overflow(page, label):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), label


def api_jobs(context, origin):
    # Use the page's browser cookie jar, including the registration redirect's
    # latest session, rather than a separate API request cookie update racing it.
    page = context.pages[0]
    return page.evaluate("async () => (await (await fetch('/clips/jobs')).json()).jobs")


def wait_new_ready(page, known):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        jobs = page.evaluate("async () => (await (await fetch('/clips/jobs')).json()).jobs")
        if any(job['id'] not in known and job['status'] == 'ready' for job in jobs):
            return
        page.wait_for_timeout(100)
    raise AssertionError({'jobs':jobs,'message':page.locator('#clip-status').inner_text()})


def wait_published(page):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        jobs = page.evaluate("async () => (await (await fetch('/clips/publishing')).json()).jobs")
        if len(jobs) == 1 and jobs[0]['status'] == 'published':
            return
        page.wait_for_timeout(100)
    raise AssertionError({'deliveries':jobs,'messages':page.locator('form[data-clip-publish] [role=status]').all_text_contents()})


def verify_account_and_connect(env, email):
    # This local test fixture simulates verification and an already authorized
    # Page; no real email is sent and no OAuth account is impersonated.
    with sqlite3.connect(env['AUTOPOST_WEB_DB']) as connection:
        workspace = connection.execute('SELECT workspace_id FROM customer_accounts WHERE email=?', (email,)).fetchone()[0]
        connection.execute('UPDATE customer_accounts SET email_verified=1 WHERE email=?', (email,))
    sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
    with patch.dict(os.environ, env):
        import meta_accounts
        meta_accounts.remember_pending(workspace, [{'page_id': '101', 'name': 'Fixture Garden Page',
            'tasks': ['CREATE_CONTENT'], 'token': 'synthetic-page-token-for-fixture-only'}])
        meta_accounts.activate_pages(workspace, ['101'])
    return workspace


def run():
    with tempfile.TemporaryDirectory(prefix='autopost-clip-workflow-') as folder:
        base = Path(folder)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('AUTOPOST_SMTP_', 'YOUTUBE_DATA_', 'CANVA_', 'STRIPE_', 'OPENAI_', 'ANTHROPIC_'))}
        env.update(AUTOPOST_WEB_DB=str(base/'app.db'), AUTOPOST_CLIP_DATA_DIR=str(base/'clips'),
            AUTOPOST_APP_PASSWORD='synthetic-owner-password123',
            AUTOPOST_WEB_SECRET='synthetic-workflow-secret-more-than-thirty-two',
            META_PAGES_ENCRYPTION_SECRET='synthetic-page-encryption-key-more-than32',
            AUTOPOST_PREVIEW_MODE='false', AUTOPOST_REQUIRE_PAYMENT='false',
            AUTOPOST_PUBLIC_LANDING='true', AUTOPOST_CLIP_ENABLED='true',
            AUTOPOST_QA_CALL_FILE=str(base/'uploads.txt'))
        logo = base/'garden.png'
        image = Image.new('RGBA', (220, 80), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((8, 8, 210, 70), fill='#217A51')
        image.save(logo)
        artifacts = Path(os.getenv('AUTOPOST_QA_ARTIFACT_DIR', str(base/'artifacts')))
        artifacts.mkdir(parents=True, exist_ok=True)
        with (base/'server.log').open('w') as log:
            server = subprocess.Popen([sys.executable, '-c', textwrap.dedent(SERVER)], cwd=ROOT,
                env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port = server.stdout.readline().strip()
                assert port.isdigit(), (base/'server.log').read_text()
                origin = 'http://127.0.0.1:' + port
                errors = []
                with sync_playwright() as playwright:
                    executable = os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
                    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'],
                        **({'executable_path': executable} if executable else {}))
                    for name, viewport in [('phone', {'width':390,'height':844}),
                                           ('desktop', {'width':1440,'height':1000})]:
                        context = browser.new_context(viewport=viewport)
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        email = 'workflow-' + name + '@example.test'
                        page.goto(origin + '/account?mode=register&next=clips')
                        page.get_by_label('Your name', exact=True).fill('Garden Creator ' + name)
                        page.get_by_label('Email', exact=True).fill(email)
                        page.get_by_label('Password', exact=True).fill(PASSWORD)
                        page.get_by_role('button', name='Create account and continue').click()
                        page.wait_for_url(origin + '/clips')
                        assert page.locator('#clip-submit').is_disabled()
                        page.get_by_role('link', name='Confirm my email', exact=True).wait_for()
                        workspace = verify_account_and_connect(env, email)
                        page.reload()
                        page.get_by_role('heading', name='Make a clip worth watching.').wait_for()
                        brand = page.locator('#clip-brand-form')
                        if not brand.is_visible():
                            page.get_by_text('Add your logo and save your business look', exact=False).click()
                        brand.locator('[name="name"]').fill('Garden ' + name)
                        brand.locator('[name="accent"]').fill('#217a51')
                        brand.locator('[name="logo"]').set_input_files(str(logo))
                        brand.get_by_role('button', name='Save my business look', exact=True).click()
                        page.wait_for_function("!document.getElementById('clip-brand-preview').hidden && document.getElementById('clip-brand-image').complete && document.getElementById('clip-brand-image').naturalWidth>0")
                        no_overflow(page, name + ' saved logo')

                        page.locator('#clip-source-mode').select_option('upload')
                        page.locator('#clip-upload input[name="video"]').set_input_files(str(ROOT/'features/clip_studio/app/assets/example-clip.mp4'))
                        page.locator('#clip-upload input[name="permission_confirmed"]').check()
                        page.get_by_role('button', name='Make my clip', exact=True).click()
                        wait_new_ready(page, set())
                        first_jobs = api_jobs(context, origin)
                        assert first_jobs, {'url':page.url,'status':page.locator('#clip-status').inner_text(),
                            'body':page.locator('body').inner_text()[-1500:]}
                        first = first_jobs[0]
                        original = base/'clips'/first['id']/'brand'/'logo.png'
                        assert original.is_file()
                        digest = hashlib.sha256(original.read_bytes()).hexdigest()
                        page.locator('#clip-jobs article').filter(has=page.locator('video')).first.wait_for()

                        # Removing the current profile logo must not change a queued
                        # clip or a caption revision made from its original kit.
                        brand.locator('[name="remove_logo"]').check()
                        brand.get_by_role('button', name='Save my business look', exact=True).click()
                        page.wait_for_function("document.getElementById('clip-brand-preview').hidden")
                        first_card = page.locator('#clip-jobs article').first
                        first_card.get_by_text('Correct captions and make a revised clip', exact=True).click()
                        first_card.get_by_role('button', name='Load captions', exact=True).click()
                        first_card.get_by_label('Caption 1', exact=True).fill('A corrected garden idea')
                        first_card.get_by_role('button', name='Render corrected captions', exact=True).click()
                        wait_new_ready(page, {first['id']})
                        revision = next(job for job in api_jobs(context, origin) if job['id'] != first['id'])
                        revised_logo = base/'clips'/revision['id']/'brand'/'logo.png'
                        assert hashlib.sha256(revised_logo.read_bytes()).hexdigest() == digest
                        assert 'A corrected garden idea' in (base/'clips'/revision['id']/'output'/'clip.srt').read_text()

                        publish = page.locator(f'form[data-clip-publish="{first["id"]}"]')
                        if not publish.is_visible():
                            publish.locator('..').locator('summary').first.click()
                        publish.get_by_label('Facebook business Page', exact=True).select_option('101')
                        publish.get_by_label('Video caption', exact=True).fill('A reviewed garden video for my business.')
                        publish.get_by_label('When should it post?', exact=True).select_option('now')
                        publish.get_by_label('I reviewed this clip and approve posting to this Page.', exact=True).check()
                        publish.get_by_role('button', name='Post video now', exact=True).click()
                        wait_published(page)
                        with sqlite3.connect(env['AUTOPOST_WEB_DB']) as connection:
                            delivery = connection.execute('SELECT status,remote_id FROM clip_publish_jobs WHERE workspace_id=?', (workspace,)).fetchone()
                        assert delivery == ('published', '555123')

                        known = {job['id'] for job in api_jobs(context, origin)}
                        page.locator('#clip-source-mode').select_option('link')
                        page.locator('#clip-upload input[name="video_url"]').fill('https://media.example.org/episode.mp4?signature=synthetic-private-query')
                        page.get_by_role('button', name='Make my clip', exact=True).click()
                        wait_new_ready(page, known)
                        all_jobs = api_jobs(context, origin)
                        assert len(all_jobs) == 3
                        assert 'synthetic-private-query' not in str(all_jobs)
                        linked = next(job for job in all_jobs if job['id'] not in known)
                        assert not (base/'clips'/linked['id']/'brand'/'logo.png').exists()
                        no_overflow(page, name + ' completed clips')
                        page.screenshot(path=str(artifacts/f'autoposter-full-clip-workflow-{name}.png'), full_page=True)

                        page.goto(origin + '/logout')
                        assert context.request.get(origin + '/clips/jobs').status == 401
                        page.goto(origin + '/account?mode=login&next=clips')
                        page.get_by_label('Email', exact=True).fill(email)
                        page.get_by_label('Password', exact=True).fill(PASSWORD)
                        page.get_by_role('button', name='Sign in and continue', exact=True).click()
                        page.wait_for_url(origin + '/clips')
                        assert len(api_jobs(context, origin)) == 3
                        for path in ['/studio', '/clips', '/account/settings']:
                            assert page.goto(origin + path).status == 200
                            no_overflow(page, name + ' shared account ' + path)
                        context.close()
                    browser.close()
                assert not errors, errors
                assert len((base/'uploads.txt').read_text().splitlines()) == 2
                assert 'Traceback' not in (base/'server.log').read_text(), (base/'server.log').read_text()[-2500:]
                print('PASS: phone/desktop customer signup and email guard; private logo save/remove; upload/ready clip; immutable-logo caption revision; reviewed Facebook delivery with one mocked upload and confirmation; background link import; shared-account logout/sign-in persistence; no JS errors or horizontal overflow. Model/decoder/provider transports are synthetic fixtures, not live-service verification.')
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    run()
