"""Real phone/desktop sidebar navigation using temporary, anonymous app data."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def run():
    with tempfile.TemporaryDirectory(prefix='autoposter-sidebar-') as directory:
        env = {**os.environ, 'AUTOPOST_WEB_DB': directory+'/app.db',
               'AUTOPOST_WEB_SECRET': 'synthetic-sidebar-session-secret-more-than32',
               'AUTOPOST_APP_PASSWORD': '', 'AUTOPOST_PREVIEW_MODE': 'true',
               'AUTOPOST_CLIP_ENABLED': 'false', 'AUTOPOST_PUBLIC_LANDING': 'true'}
        code = "import app_mvp;from werkzeug.serving import make_server;s=make_server('127.0.0.1',0,app_mvp.app,threaded=True);print(s.server_port,flush=True);s.serve_forever()"
        with open(directory+'/server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=env,
                                      stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port = server.stdout.readline().strip()
                assert port.isdigit(), Path(directory+'/server.log').read_text()[-1500:]
                origin = 'http://127.0.0.1:'+port
                errors = []
                with sync_playwright() as p:
                    executable = os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
                    browser = p.chromium.launch(headless=True, args=['--no-sandbox'],
                        **({'executable_path': executable} if executable else {}))
                    for name, size in [('phone', {'width':390,'height':844}),
                                       ('desktop', {'width':1280,'height':900})]:
                        context = browser.new_context(viewport=size)
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        assert page.goto(origin+'/welcome').status == 200
                        tools = page.get_by_role('button', name='☰ Tools', exact=True)
                        tools.wait_for(timeout=15000)
                        assert tools.get_attribute('aria-expanded') == 'false'
                        tools.click()
                        assert tools.get_attribute('aria-expanded') == 'true'
                        drawer = page.locator('#'+tools.get_attribute('aria-controls'))
                        drawer.get_by_role('link', name='Clip Studio', exact=True).wait_for()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        page.keyboard.press('Escape')
                        assert tools.get_attribute('aria-expanded') == 'false'
                        assert tools.evaluate('(node) => document.activeElement === node')
                        tools.click()
                        page.mouse.click(size['width']-8, size['height']//2)
                        assert tools.get_attribute('aria-expanded') == 'false'
                        tools.click()
                        drawer.locator('a[href="/account?mode=login"]').click()
                        page.wait_for_url('**/account?mode=login')
                        page.get_by_label('Email', exact=True).wait_for()
                        page.get_by_role('button', name='Sign in and continue', exact=True).wait_for()
                        tools = page.get_by_role('button', name='☰ Tools', exact=True)
                        tools.click()
                        drawer = page.locator('#'+tools.get_attribute('aria-controls'))
                        drawer.locator('a[href="/account?mode=register"]').click()
                        page.wait_for_url('**/account?mode=register')
                        page.get_by_label('Your name', exact=True).wait_for()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        context.close()
                    browser.close()
                assert not errors, errors
                print('PASS: phone/desktop Tools drawer opens/closes, Escape restores focus, backdrop closes, sign-in and registration links open real forms; no JavaScript errors or horizontal overflow.')
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    run()
