"""Account forms must paint and respond while optional styling is unavailable.

Holds every stylesheet request open until AFTER the browser has painted the
form and handled its password control. Synthetic local storage only; no account
creation, model downloads, email, or external provider calls.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
SERVER = """
import app_mvp
from werkzeug.serving import make_server
server=make_server('127.0.0.1', 0, app_mvp.app, threaded=True)
print(server.server_port, flush=True)
server.serve_forever()
"""


def run():
    with tempfile.TemporaryDirectory(prefix='autoposter-first-paint-') as directory:
        env = {**os.environ, 'AUTOPOST_WEB_DB': directory+'/app.db',
               'AUTOPOST_WEB_SECRET': 'synthetic-first-paint-secret-more-than32',
               'AUTOPOST_CLIP_ENABLED': 'false', 'AUTOPOST_PUBLIC_LANDING': 'true'}
        with open(directory+'/server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT,
                env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port = server.stdout.readline().strip()
                assert port.isdigit(), Path(directory+'/server.log').read_text()[-1500:]
                with sync_playwright() as playwright:
                    executable = os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
                    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'],
                        **({'executable_path': executable} if executable else {}))
                    try:
                        for name, viewport in [('phone', {'width':390,'height':844}),
                                               ('desktop', {'width':1280,'height':900})]:
                            for mode in ('login', 'register'):
                                context = browser.new_context(viewport=viewport)
                                page = context.new_page()
                                held = []
                                errors = []
                                page.on('pageerror', lambda error: errors.append(str(error)))
                                page.route('**/*.css', lambda route: held.append(route))
                                cdp = context.new_cdp_session(page)
                                cdp.send('Network.enable')
                                cdp.send('Network.emulateNetworkConditions', {
                                    'offline':False, 'latency':300,
                                    'downloadThroughput':50*1024,
                                    'uploadThroughput':25*1024,
                                    'connectionType':'cellular3g'})
                                try:
                                    response = page.goto('http://127.0.0.1:'+port+
                                        '/account?mode='+mode, wait_until='commit')
                                    assert response.status == 200
                                    page.wait_for_function("""() =>
                                        performance.getEntriesByType('paint').some(
                                            entry => entry.name === 'first-contentful-paint') &&
                                        document.getElementById('ap-tool-sidebar-script') &&
                                        document.getElementById('ap-tool-drawer')
                                    """, timeout=10000)
                                    assert held, 'The optional stylesheet was not held open'
                                    assert all('/static/tool-sidebar.css' in route.request.url
                                               for route in held), [route.request.url for route in held]
                                    # Execute the real click handler before releasing CSS;
                                    # this also proves deferred account.js could initialize.
                                    visible = page.evaluate("""() => {
                                        document.getElementById('show-password').click();
                                        return document.getElementById('password').type === 'text';
                                    }""")
                                    assert visible, 'Password control waited for optional styling'
                                    assert page.locator('#email').is_visible()
                                    assert page.locator('#account-submit').is_enabled()
                                    assert not errors, errors
                                    paint = page.evaluate("""Math.round(performance.getEntriesByType(
                                        'paint').find(entry => entry.name ===
                                        'first-contentful-paint').startTime)""")
                                    print(f'PASS {name} {mode}: FCP {paint}ms and password control '
                                          'responded before any stylesheet was delivered.', flush=True)
                                finally:
                                    for route in held:
                                        route.continue_()
                                    context.close()
                    finally:
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    run()
