"""Temporary-data customer signup/import/draft and creative workbench browser check."""
import os,subprocess,tempfile,sys,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
root=str(Path(__file__).resolve().parents[2])
with tempfile.TemporaryDirectory(prefix='autopost-complete-') as folder:
 env={**os.environ,'AUTOPOST_WEB_DB':folder+'/app.db','AUTOPOST_APP_PASSWORD':'synthetic-owner-password123','AUTOPOST_WEB_SECRET':'synthetic-test-secret-longer-than-thirty-two','AUTOPOST_REQUIRE_PAYMENT':'true','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_PUBLIC_LANDING':'true'}
 code="import app_mvp;from werkzeug.serving import make_server;app_mvp.init_db();s=make_server('127.0.0.1',0,app_mvp.app,threaded=True);print(s.server_port,flush=True);s.serve_forever()"
 with open(folder+'/logs','w') as log:
  server=subprocess.Popen([sys.executable,'-c',code],cwd=root,env=env,stdout=subprocess.PIPE,stderr=log,text=True)
  try:
   port=server.stdout.readline().strip();assert port.isdigit();origin='http://127.0.0.1:'+port
   with sync_playwright() as p:
    browser_path=os.getenv('HQ_CHROME_PATH') or shutil.which('chromium');browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':browser_path} if browser_path else {}))
    errors=[]
    for name,viewport in [('phone',{'width':390,'height':844}),('desktop',{'width':1440,'height':1000})]:
     ctx=browser.new_context(viewport=viewport);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
     page.goto(origin+'/start');page.locator('input[name=platforms][value=facebook]').check();page.locator('#next').click();page.locator('#message').fill('Our new shop is open Saturday. Come and visit.');page.locator('#next').click();page.locator('#next').click();page.locator('#complete-setup').click();page.wait_for_url('**/account?next=setup')
     page.get_by_label('Your name',exact=True).fill('Customer '+name);page.get_by_label('Email',exact=True).fill(name+'@example.test');page.get_by_label('Password',exact=True).fill('synthetic-customer-password123');page.get_by_role('button',name='Create account and continue').click();page.wait_for_url('**/?imported=1');page.locator('#imported-setups').get_by_text('Our new shop is open Saturday. Come and visit.',exact=True).wait_for()
     page.get_by_role('button',name='Prepare post for review').click();page.wait_for_url('**/#posts');page.locator('#posts').get_by_text('Our new shop is open Saturday. Come and visit.',exact=True).wait_for()
     for path in ['/studio','/connections','/reports']:
      r=page.goto(origin+path);assert r.status==200,(path,r.status);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),path
     page.goto(origin+'/studio');page.get_by_label('Business or creator name').fill('My business');page.get_by_label('Location',exact=True).fill('Fort Wayne');page.get_by_role('button',name='Save my style').click();page.get_by_label('Business or creator name').fill('My business');assert page.get_by_label('Location',exact=True).input_value()=='Fort Wayne'
     page.get_by_role('button',name='Help me draft it').click();page.get_by_role('status').filter(has_text='Describe the post').wait_for()
     artifacts=Path(os.getenv('AUTOPOST_QA_ARTIFACT_DIR',str(Path(tempfile.gettempdir())/'autopost-customer-qa')));artifacts.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(artifacts/('autoposter-studio-'+name+'.png')),full_page=True)
     ctx.close()
    browser.close();assert not errors,errors
   print('PASS: review -> customer signup -> automatic scoped import -> real prepared draft; creative profile persistence; studio/connections/reports on phone and desktop; no JS errors or overflow.')
  finally:
   server.terminate();server.wait(timeout=10)
