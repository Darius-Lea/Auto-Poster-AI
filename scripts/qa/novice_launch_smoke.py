"""Idea-only customer journey using temporary data; no paid/external calls."""
import os,shutil,subprocess,sys,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='autopost-novice-') as folder:
 env={**os.environ,'AUTOPOST_WEB_DB':folder+'/app.db','AUTOPOST_APP_PASSWORD':'synthetic-owner-password123','AUTOPOST_WEB_SECRET':'synthetic-test-secret-longer-than-thirty-two','AUTOPOST_REQUIRE_PAYMENT':'true','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_PUBLIC_LANDING':'true'}
 code="import app_mvp;from werkzeug.serving import make_server;app_mvp.init_db();s=make_server('127.0.0.1',0,app_mvp.app,threaded=True);print(s.server_port,flush=True);s.serve_forever()"
 with open(folder+'/server.log','w') as log:
  server=subprocess.Popen([sys.executable,'-c',code],cwd=root,env=env,stdout=subprocess.PIPE,stderr=log,text=True)
  try:
   port=server.stdout.readline().strip();assert port.isdigit();origin='http://127.0.0.1:'+port
   with sync_playwright() as p:
    executable=os.getenv('HQ_CHROME_PATH') or shutil.which('chromium');browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':executable} if executable else {}));errors=[]
    for name,size in [('phone',{'width':390,'height':844}),('desktop',{'width':1440,'height':1000})]:
     context=browser.new_context(viewport=size);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
     page.goto(origin+'/welcome');page.get_by_role('link',name='Build my first plan',exact=True).first.click();page.wait_for_url('**/launch')
     page.get_by_label('Business or working name',exact=True).fill('My Bread Idea');page.get_by_label('Describe your idea in your own words').fill('I want to make home baked bread for busy families.');page.get_by_label('Who would find it useful?').fill('Busy families nearby');page.get_by_label('What would they receive?').fill('A weekly loaf of bread');page.get_by_role('button',name='Build my first plan',exact=True).click();page.locator('.launch-caption').first.wait_for();assert page.locator('.launch-caption').count()==7
     page.get_by_label('Day 1 caption',exact=True).fill('We are developing My Bread Idea. What would make it useful to you?')
     page.get_by_role('button',name='Save seven drafts to my account',exact=True).click();page.wait_for_url('**/account?next=launch')
     page.get_by_label('Your name',exact=True).fill('New Founder');page.get_by_label('Email',exact=True).fill('novice-'+name+'@example.test');page.get_by_label('Password',exact=True).fill('synthetic-novice-password123');page.get_by_role('button',name='Create account and continue').click();page.wait_for_url('**/launch');page.locator('#save-status').filter(has_text='Your seven internal drafts are saved').wait_for();page.get_by_role('link',name='Open my saved drafts',exact=False).click();page.wait_for_url('**/#posts');assert page.locator('#posts .post').count()==7
     page.get_by_role('link',name='Use this draft',exact=True).last.click();page.wait_for_url('**/studio?draft_id=*');page.get_by_label('Your editable caption',exact=True).fill('My retained caption after editing.');page.locator('#draft-save-status').filter(has_text='Draft saved to your workspace').wait_for();page.goto(origin+'/connections');page.goto(origin+'/studio');assert page.get_by_label('Your editable caption',exact=True).input_value()=='My retained caption after editing.'
     page.goto(origin+'/control');page.get_by_role('button',name='Pause all publishing',exact=True).click();page.get_by_role('button',name='Resume publishing',exact=True).wait_for();assert 'Booking photos' not in page.locator('body').inner_text();assert 'GitHub Actions control bridge' not in page.locator('body').inner_text()
     page.get_by_role('button',name='Resume publishing',exact=True).click();page.get_by_role('button',name='Pause all publishing',exact=True).wait_for()
     for path in ['/help','/privacy','/terms','/account/settings','/account/forgot']:
      r=page.goto(origin+path);assert r.status==200,(path,r.status);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),path
     page.goto(origin+'/launch');page.get_by_role('button',name='Build my first plan',exact=True).click();page.locator('#my-plan').wait_for(state='visible');out=Path(os.getenv('AUTOPOST_QA_ARTIFACT_DIR',str(Path(tempfile.gettempdir())/'autopost-novice-qa')));out.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/('autoposter-novice-plan-'+name+'.png')),full_page=True);context.close()
    browser.close();assert not errors,errors
   print('PASS: novice idea -> seven editable drafts -> signup -> saved backend drafts; edit/restore, true publishing pause, owner-only settings hidden, public help/privacy/terms/recovery; phone/desktop no overflow or JS errors.')
  finally:
   server.terminate();server.wait(timeout=10)
