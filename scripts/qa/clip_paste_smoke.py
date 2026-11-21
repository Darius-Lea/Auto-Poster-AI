"""A real, unavailable service must still let a customer paste a video URL."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIDEO = 'https://media.example.org/my-video.mp4?signature=synthetic-private-query'


def run():
    with tempfile.TemporaryDirectory(prefix='autoposter-paste-') as folder:
        env = {**os.environ, 'AUTOPOST_WEB_DB':folder+'/app.db',
               'AUTOPOST_CLIP_DATA_DIR':folder+'/clips',
               'AUTOPOST_WEB_SECRET':'synthetic-paste-secret-longer-than-thirty-two',
               'AUTOPOST_APP_PASSWORD':'', 'AUTOPOST_PREVIEW_MODE':'true',
               'AUTOPOST_CLIP_ENABLED':'false', 'AUTOPOST_CUSTOMER_SIGNUP_ENABLED':'true'}
        code = "import app_mvp;from werkzeug.serving import make_server;s=make_server('127.0.0.1',0,app_mvp.app,threaded=True);print(s.server_port,flush=True);s.serve_forever()"
        with open(folder+'/server.log','w') as log:
            server = subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,
                                      stdout=subprocess.PIPE,stderr=log,text=True)
            try:
                port=server.stdout.readline().strip()
                assert port.isdigit(),Path(folder+'/server.log').read_text()[-1500:]
                origin='http://127.0.0.1:'+port
                with sync_playwright() as playwright:
                    executable=os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
                    browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'],
                        **({'executable_path':executable} if executable else {}))
                    for name,size in [('phone',{'width':390,'height':844}),('desktop',{'width':1280,'height':900})]:
                        context=browser.new_context(viewport=size,permissions=['clipboard-read','clipboard-write'])
                        page=context.new_page();errors=[]
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/account?mode=register&next=clips')
                        page.get_by_label('Your name',exact=True).fill('Paste Customer')
                        page.get_by_label('Email',exact=True).fill('paste-'+name+'@example.test')
                        page.get_by_label('Password',exact=True).fill('synthetic-paste-password123')
                        page.get_by_role('button',name='Create account and continue',exact=True).click()
                        page.wait_for_url(origin+'/clips')
                        field=page.locator('#clip-video-url')
                        assert field.is_visible() and field.is_enabled() and field.is_editable()
                        field.click()
                        assert field.evaluate('(node)=>document.activeElement===node')
                        page.evaluate('(text)=>navigator.clipboard.writeText(text)',VIDEO)
                        page.keyboard.press('Control+V')
                        page.wait_for_function('(expected)=>document.getElementById("clip-video-url").value===expected',arg=VIDEO)
                        assert page.locator('#clip-submit').is_disabled(), 'Unavailable rendering was accidentally enabled'
                        with page.expect_response(lambda r:r.url==origin+'/clips/source/check') as reply:
                            page.get_by_role('button',name='Check link',exact=True).click()
                        assert reply.value.status==200,reply.value.status
                        data=reply.value.json()
                        assert data['valid'] is True and data['render_available'] is False,data
                        assert 'synthetic-private-query' not in str(data)
                        page.locator('#clip-link-status').filter(has_text=data['message']).wait_for()
                        assert page.evaluate("async()=> (await (await fetch('/clips/jobs')).json()).jobs.length") == 0
                        page.locator('#clip-source-mode').select_option('upload')
                        assert field.is_visible() and field.is_enabled()
                        page.locator('#clip-source-mode').select_option('link')
                        assert field.input_value()==VIDEO
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                        assert not errors,errors
                        context.close()
                    browser.close()
                print('PASS: phone/desktop real clipboard paste into an enabled visible link box, unavailable-rendering guard, CSRF link check without jobs/quota or URL disclosure, source switching preserves link, no JavaScript errors or overflow. No download/model/provider was used.')
            finally:
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':run()
