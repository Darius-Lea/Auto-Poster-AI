"""Real customer account access through a direct WSGI import and fresh storage.

This deliberately never calls init_db(): it reproduces the deployment path
that used to serve the account form and then fail the first POST with missing
tables. All accounts are synthetic; no mail, models or providers are used.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
PASSWORD = 'synthetic-account-access-password123'
SERVER = r'''
import app_mvp
import requests
import smtplib
from werkzeug.serving import make_server

def no_external(*args, **kwargs):
    raise AssertionError('The account access fixture attempted an external service')
requests.sessions.Session.request = no_external
smtplib.SMTP = no_external
smtplib.SMTP_SSL = no_external
# The direct app import must support the first real signup by itself.
server = make_server('127.0.0.1', 0, app_mvp.app, threaded=True)
print(server.server_port, flush=True)
server.serve_forever()
'''


def no_overflow(page, label):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), label


def fill_login(page, email):
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Password', exact=True).fill(PASSWORD)


def hold_one_submit(page):
    held = []

    def intercept(route):
        if route.request.method == 'POST' and not held:
            held.append(route)
        else:
            route.continue_()

    page.route('**/account', intercept)
    # requestSubmit fires normal browser validation and the same submit event
    # as clicking the primary button without waiting for the held navigation.
    feedback=page.locator('form[action="/account"]').evaluate('''form => {
        form.requestSubmit();
        const button=form.querySelector('button[type="submit"]');
        return {disabled:button.disabled,label:button.textContent};
    }''')
    assert feedback['disabled'], 'Account submission has no pending state'
    label=feedback['label'].lower()
    assert any(word in label for word in ('creating','signing','please wait')), label
    deadline = time.monotonic() + 5
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert held, 'Account submission was never issued'
    held[0].continue_()
    page.unroute('**/account', intercept)


def legacy_storage(path):
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE app_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,
            platform TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL,
            remote_id TEXT,error TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        connection.execute("INSERT INTO app_posts(content,platform,due_at,status) VALUES('Preserve the legacy owner draft','draft','2030-01-01T00:00:00+00:00','draft_ready')")


def run():
    artifacts = Path(os.getenv('AUTOPOST_QA_ARTIFACT_DIR', str(Path(tempfile.gettempdir())/'autopost-account-access')))
    artifacts.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        executable = os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'],
            **({'executable_path': executable} if executable else {}))
        try:
            for name, viewport in [('phone', {'width':390,'height':844}), ('desktop', {'width':1440,'height':1000})]:
                with tempfile.TemporaryDirectory(prefix='autopost-account-access-') as directory:
                    folder = Path(directory)
                    database = folder/'accounts.db'
                    if name == 'desktop':
                        legacy_storage(database)
                    environment = {**os.environ,
                        'AUTOPOST_WEB_DB':str(database), 'AUTOPOST_CLIP_DATA_DIR':str(folder/'clips'),
                        'AUTOPOST_WEB_SECRET':'synthetic-account-access-secret-more-than-thirty-two',
                        'AUTOPOST_APP_PASSWORD':'', 'AUTOPOST_PREVIEW_MODE':'true',
                        'AUTOPOST_CLIP_ENABLED':'false', 'AUTOPOST_PUBLIC_LANDING':'true',
                        'AUTOPOST_CUSTOMER_SIGNUP_ENABLED':'true', 'AUTOPOST_REQUIRE_PAYMENT':'true',
                        'AUTOPOST_SMTP_HOST':'', 'AUTOPOST_PUBLIC_URL':'',
                    }
                    log_path = folder/'server.log'
                    with log_path.open('w') as log:
                        server = subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT,
                            env=environment, stdout=subprocess.PIPE, stderr=log, text=True)
                        context = None
                        try:
                            port = server.stdout.readline().strip()
                            assert port.isdigit(), log_path.read_text()[-2000:]
                            origin = 'http://127.0.0.1:' + port
                            context = browser.new_context(viewport=viewport)
                            page = context.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            email = 'account-access-' + name + '@example.test'

                            response = page.goto(origin+'/account?mode=register')
                            assert response.status == 200
                            page.get_by_label('Your name', exact=True).fill('New Customer ' + name)
                            page.get_by_label('Email', exact=True).fill(email)
                            create = page.get_by_role('button', name='Create account and continue', exact=True)
                            create.click()
                            assert create.is_enabled(), 'An invalid account form became stuck'
                            fill_login(page, email)
                            started = time.monotonic()
                            hold_one_submit(page)
                            page.wait_for_url(origin+'/')
                            page.get_by_role('heading', name='Prepare your next post').wait_for()
                            measurements.append((name+' signup', round(time.monotonic()-started, 3)))
                            no_overflow(page, name+' first dashboard')
                            for path in ('/studio', '/clips', '/account/settings'):
                                assert page.goto(origin+path).status == 200, path
                                no_overflow(page, name+path)

                            page.goto(origin+'/logout')
                            page.goto(origin+'/account?mode=login')
                            fill_login(page, email)
                            # A real SQLite writer lock must become an actionable
                            # bounded error, never an account-creation duplicate or500.
                            held_database = sqlite3.connect(database, timeout=1)
                            held_database.execute('BEGIN IMMEDIATE')
                            try:
                                with page.expect_response(lambda r: r.url==origin+'/account' and r.request.method=='POST', timeout=10000) as reply:
                                    page.get_by_role('button', name='Sign in and continue', exact=True).click()
                                assert reply.value.status == 503, reply.value.status
                                page.get_by_role('alert').wait_for()
                                assert 'internal server error' not in page.locator('body').inner_text().lower()
                                assert page.get_by_label('Email', exact=True).input_value() == email
                                assert page.get_by_label('Password', exact=True).input_value() == ''
                                assert page.get_by_role('button', name='Sign in and continue', exact=True).is_enabled()
                                no_overflow(page, name+' retry message')
                            finally:
                                held_database.rollback()
                                held_database.close()
                            fill_login(page, email)
                            started = time.monotonic()
                            hold_one_submit(page)
                            page.wait_for_url(origin+'/')
                            page.get_by_role('heading', name='Prepare your next post').wait_for()
                            measurements.append((name+' signin after retry', round(time.monotonic()-started, 3)))
                            with sqlite3.connect(database) as connection:
                                assert connection.execute('SELECT COUNT(*) FROM customer_accounts WHERE email=?',(email,)).fetchone()[0] == 1
                                if name == 'desktop':
                                    row = connection.execute("SELECT workspace_id,content FROM app_posts WHERE content='Preserve the legacy owner draft'").fetchone()
                                    assert row == ('owner','Preserve the legacy owner draft'), row
                            page.screenshot(path=str(artifacts/('autoposter-account-access-'+name+'.png')),full_page=True)
                            assert not errors, errors
                        finally:
                            if context:
                                context.close()
                            server.terminate()
                            server.wait(timeout=10)
                            shutil.copyfile(log_path, artifacts/('autoposter-account-access-'+name+'.log'))
        finally:
            browser.close()
    print('PASS: direct WSGI import without explicit database initialization; fresh and legacy database signup; real account logout/signin/private tools; immediate pending feedback; bounded storage-busy503 and successful retry; no duplicate account, page errors or overflow. No external services used.')
    print('Observed local browser durations (seconds): '+', '.join(label+'='+str(seconds) for label,seconds in measurements))


if __name__ == '__main__':
    run()
