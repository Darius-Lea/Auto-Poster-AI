"""Offline desktop/mobile setup smoke check; uses temporary data and no worker.

Run after installing requirements-app.txt and a Playwright Chromium browser.
"""
import os,subprocess,tempfile,json,struct,sys,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
root=str(Path(__file__).resolve().parents[2])
artifacts=Path(os.getenv('AUTOPOST_QA_ARTIFACT_DIR',str(Path(tempfile.gettempdir())/'autopost-qa-artifacts')));artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='autopost-ux-') as folder:
 env={**os.environ,'AUTOPOST_WEB_DB':folder+'/review.db','AUTOPOST_APP_PASSWORD':'synthetic-private-beta-review-password','AUTOPOST_WEB_SECRET':'synthetic-review-secret-longer-than-thirty-two','AUTOPOST_REQUIRE_PAYMENT':'true','AUTOPOST_PREVIEW_MODE':'true','AUTOPOST_PUBLIC_LANDING':'true'}
 code="import app_mvp; from werkzeug.serving import make_server; app_mvp.init_db(); s=make_server('127.0.0.1',0,app_mvp.app,threaded=True); print(s.server_port,flush=True); s.serve_forever()"
 with open(folder+'/server.log','w') as logs:
  server=subprocess.Popen([sys.executable,'-c',code],cwd=root,env=env,stdout=subprocess.PIPE,stderr=logs,text=True)
  try:
   port=server.stdout.readline().strip()
   if not port.isdigit():raise RuntimeError('Review server did not start')
   origin='http://127.0.0.1:'+port
   with sync_playwright() as p:
    browser_path=os.getenv('HQ_CHROME_PATH') or shutil.which('chromium')
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'],**({'executable_path':browser_path} if browser_path else {}))
    errors=[]
    for name,size in [('desktop',{'width':1440,'height':1000}),('mobile',{'width':390,'height':844})]:
     context=browser.new_context(viewport=size)
     page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
     page.goto(origin+'/welcome');page.wait_for_function("[...document.images].every(i=>i.complete && i.naturalWidth>0)");page.screenshot(path=str(artifacts/('autoposter-branded-home-'+name+'.png')),full_page=True);page.get_by_role('link',name='Set up a text post',exact=True).click()
     page.get_by_role('heading',name='Your next post. Planned your way.').wait_for()
     page.wait_for_function("[...document.images].every(i=>i.complete && i.naturalWidth>0)");first=page.locator('body').inner_text()
     assert 'before you pay' not in first.lower()
     assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
     page.screenshot(path=str(artifacts/('autoposter-branded-setup-'+name+'.png')),full_page=True)
     page.locator('input[name=platforms][value=facebook]').check();page.locator('#next').click()
     page.locator('#message').fill('New arrivals are here. Explore this week’s collection.')
     page.locator('#next').click();page.locator('#next').click()
     page.get_by_role('heading',name='Review your setup').wait_for()
     assert 'active subscription' in page.locator('body').inner_text()
     assert page.locator('#review-list').inner_text().find('New arrivals are here.')>=0
     page.locator('#save-setup').click();page.locator('#saved-success').wait_for(state='visible')
     page.reload();assert page.locator('#message').input_value().startswith('New arrivals')
     response=context.request.post(origin+'/login',form={'password':env['AUTOPOST_APP_PASSWORD']});assert response.ok
     page.goto(origin+'/');page.get_by_role('heading',name='Prepare your next post').wait_for()
     assert 'Plan first, pay when ready' not in page.locator('body').inner_text()
     assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
     page.screenshot(path=str(artifacts/('autoposter-branded-dashboard-'+name+'.png')),full_page=True)
     page.goto(origin+'/automations');page.wait_for_function("[...document.images].every(i=>i.complete && i.naturalWidth>0)");assert 'SET UP BEFORE YOU PAY' not in page.locator('body').inner_text()
     context.close()
    asset_context=browser.new_context()
    manifest=asset_context.request.get(origin+'/manifest.webmanifest').json()
    for icon in manifest['icons']:
     r=asset_context.request.get(origin+icon['src']);assert r.ok,icon
     if icon['type']=='image/png':
      w,h=struct.unpack('>II',r.body()[16:24]);assert str(w)+'x'+str(h)==icon['sizes']
    assert len([i for i in manifest['icons'] if i['purpose']=='maskable'])==1
    for path in ['/static/brand/apple-touch-icon.png','/static/brand/mark.svg','/static/brand/brand.css','/static/brand/publishing-orbit.png','/static/brand/social-card.png']:
     assert asset_context.request.get(origin+path).ok,path
    login=asset_context.new_page()
    login.goto(origin+'/login');login.wait_for_function('[...document.images].every(i=>i.complete && i.naturalWidth>0)');assert login.locator('.brand-icon').count()==1
    login.screenshot(path=str(artifacts/'autoposter-branded-login.png'))
    asset_context.close();browser.close()
    assert not errors,errors
    print('PASS: anonymous brand assets, manifest PNG sizes/maskable icon, login logo; desktop/mobile landing → four setup steps → final subscription disclosure → saved draft persists; owner dashboard and automation screens; no horizontal overflow or JavaScript errors. No worker or publishing started.')
  finally:
   server.terminate();server.wait(timeout=10)
