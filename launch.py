"""An idea-first marketing worksheet and an authenticated, idempotent draft handoff."""
from __future__ import annotations
import hashlib,io,json,os,re,secrets,sqlite3,time
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
from flask import Blueprint,abort,jsonify,request,session,render_template,send_file
launch_bp=Blueprint('launch',__name__)

def db():
 c=sqlite3.connect(os.getenv('AUTOPOST_WEB_DB','autopost_web.db'),timeout=15);c.row_factory=sqlite3.Row;return c

def init():
 with db() as c:c.executescript('''CREATE TABLE IF NOT EXISTS business_launch_plans(id INTEGER PRIMARY KEY,workspace_id TEXT NOT NULL,plan_key TEXT NOT NULL,brief_json TEXT NOT NULL,created INTEGER NOT NULL,UNIQUE(workspace_id,plan_key));CREATE TABLE IF NOT EXISTS business_launch_posts(plan_id INTEGER NOT NULL,day INTEGER NOT NULL,post_id INTEGER NOT NULL,PRIMARY KEY(plan_id,day));''')

def _text(values,name,label,minimum=0,maximum=400):
 value=values.get(name,'')
 if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:raise ValueError('Enter '+label+'.')
 return value.strip()

def validate(values):
 if not isinstance(values,dict):raise ValueError('Start with a business idea and an offer.')
 result={k:_text(values,k,label,lo,hi) for k,label,lo,hi in [('name','a business or working name',2,80),('idea','your idea in one or two sentences',10,1000),('audience','who this is for',3,200),('offer','what you want to offer',3,500),('difference','what you want to do differently',0,400),('location','a location, or leave it blank',0,120),('contact','a contact or website, or leave it blank',0,300)]}
 result['stage']=values.get('stage','idea');result['goal']=values.get('goal','feedback')
 if result['stage'] not in ('idea','ready') or result['goal'] not in ('feedback','awareness','inquiries'):raise ValueError('Choose your stage and first goal.')
 if '://' in result['contact']:
  u=urlparse(result['contact'])
  if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Use an HTTPS website link without login details.')
 try:
  date=datetime.fromisoformat(str(values.get('start_date',''))).date()
  if not datetime.now(timezone.utc).date()<=date<=datetime.now(timezone.utc).date()+timedelta(days=365):raise ValueError
 except (ValueError,TypeError):raise ValueError('Choose a start date between today and next year.') from None
 result['start_date']=date.isoformat();return result

def build_plan(values):
 b=validate(values);name=b['name'];offer=b['offer'];aud=b['audience'];location=(' in '+b['location']) if b['location'] else ''
 action=('Learn more or get in touch: '+b['contact']) if b['contact'] else 'Tell us what would make this useful for you.'
 if b['goal']=='feedback':action='Tell us which part would be useful and what you would change.'
 elif b['goal']=='awareness':action='Follow along as we explain the idea and answer your questions.'
 intro=('We’re developing '+name+' for '+aud+location+'.') if b['stage']=='idea' else (name+' is for '+aud+location+'.')
 difference=(' What we want to do differently: '+b['difference']) if b['difference'] else ''
 captions=[intro+' The idea: '+offer+'. '+action,
  'A question for '+aud+location+': what is the hardest part of finding the right option for your needs? We’re listening as we work on '+name+'.',
  'The offer '+('we’re developing' if b['stage']=='idea' else 'we want to introduce')+': '+offer+'.'+difference+' '+action,
  'Help shape '+name+': which detail matters most to you—what it includes, how it works, or how to get started? Share your questions.',
  'For anyone just discovering '+name+': '+offer+'. It is intended for '+aud+'. What would you like us to explain more clearly?',
  'We’d like feedback on '+name+'. '+offer+'.'+difference+' '+action,
  'Our next step for '+name+' is to learn what matters most to '+aud+'. What should we focus on next? '+action]
 titles=['Introduce the idea','Listen to your audience','Explain the offer','Invite questions','Make it clear','Ask for useful feedback','Choose your next experiment']
 start=datetime.fromisoformat(b['start_date']).date()
 return {'brief':b,'positioning':intro+' '+offer+'.','product_steps':['Write the smallest version of your offer that someone can understand.','Show a simple example or prototype to a few people in your intended audience.','Ask what is useful, confusing and missing before committing to promises or a price.','Record the feedback and choose one improvement to test next.'], 'week':[{'day':i+1,'date':(start+timedelta(days=i)).isoformat(),'title':titles[i],'caption':text} for i,text in enumerate(captions)],'notice':'These are editable starting drafts based on your answers, not AI research or evidence of demand. Check every claim before publishing. This tool helps plan and market your offer; it does not manufacture products, build an online store or guarantee viral reach.'}

@launch_bp.route('/launch')
def begin():
 if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(32)
 pages=[]
 if session.get('authorized'):
  from app_mvp import current_workspace
  import meta_accounts
  pages=meta_accounts.list_pages(current_workspace())
 return render_template('launch.html',csrf=session['csrf'],today=datetime.now(timezone.utc).date().isoformat(),pages=pages)

@launch_bp.route('/launch/build',methods=['POST'])
def build():
 import customer_accounts
 if not session.get('csrf') or not secrets.compare_digest(request.form.get('_csrf',''),session['csrf']):return jsonify(error='Refresh this page before creating your plan.'),403
 try:
  customer_accounts.attempt('launch-plan',request.remote_addr or 'local',limit=20,window=300)
  return jsonify(build_plan(request.form.to_dict()))
 except PermissionError as exc:return jsonify(error=str(exc)),429
 except ValueError as exc:return jsonify(error=str(exc)),400

@launch_bp.route('/launch/import',methods=['POST'])
def import_plan():
 from app_mvp import current_workspace
 ws=current_workspace()
 try:
  values=json.loads(request.form.get('brief','{}'));plan=build_plan(values);key=str(request.form.get('plan_key',''))
  if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Create your plan again before saving it.')
  captions=json.loads(request.form.get('captions','[]'))
  if not isinstance(captions,list) or len(captions)!=7 or any(not isinstance(s,str) or not 2<=len(s.strip())<=2000 for s in captions):raise ValueError('Review all seven editable drafts before saving.')
  init()
  with db() as c:
   c.execute('BEGIN IMMEDIATE');c.execute('INSERT OR IGNORE INTO business_launch_plans(workspace_id,plan_key,brief_json,created) VALUES(?,?,?,?)',(ws,key,json.dumps(plan['brief']),int(time.time())))
   row=c.execute('SELECT id FROM business_launch_plans WHERE workspace_id=? AND plan_key=?',(ws,key)).fetchone();created=0
   for day,text in enumerate(captions,1):
    existing=c.execute('SELECT m.post_id,p.content,p.platform,p.status FROM business_launch_posts m JOIN app_posts p ON p.id=m.post_id WHERE m.plan_id=? AND m.day=? AND p.workspace_id=?',(row['id'],day,ws)).fetchone()
    if existing:
     if (existing['platform']!='draft' or existing['status']!='draft_ready') and existing['content']!=text.strip():raise ValueError('Some posts are already scheduled or changed. Manage those posts individually before changing this plan.')
     c.execute("UPDATE app_posts SET content=? WHERE id=? AND workspace_id=? AND platform='draft' AND status='draft_ready'",(text.strip(),existing['post_id'],ws));continue
    when=plan['week'][day-1]['date']+'T12:00:00+00:00'
    post=c.execute("INSERT INTO app_posts(content,platform,due_at,status,frequency,workspace_id) VALUES(?,'draft',?,'draft_ready','once',?)",(text.strip(),when,ws)).lastrowid
    c.execute('INSERT INTO business_launch_posts VALUES(?,?,?)',(row['id'],day,post));created+=1
   c.execute('UPDATE business_launch_plans SET brief_json=? WHERE id=?',(json.dumps(plan['brief']),row['id']))
  return jsonify(plan_id=row['id'],created=created,total=7,message='Your seven internal drafts are saved. No posts are scheduled or published.')
 except (ValueError,TypeError,json.JSONDecodeError) as exc:return jsonify(error=str(exc)),400

@launch_bp.route('/launch/export',methods=['POST'])
def export_plan():
 if not session.get('csrf') or not secrets.compare_digest(request.form.get('_csrf',''),session['csrf']):abort(403)
 try:plan=build_plan(json.loads(request.form.get('brief','{}')))
 except (ValueError,TypeError):abort(400,'Create a valid plan before downloading it.')
 return send_file(io.BytesIO(json.dumps(plan,ensure_ascii=False,indent=2).encode()),mimetype='application/json',as_attachment=True,download_name='my-first-marketing-plan.json')

@launch_bp.route('/launch/schedule',methods=['POST'])
def schedule_week():
 from app_mvp import current_workspace,preview_mode
 from zoneinfo import ZoneInfo
 import app_control,meta_accounts,plan_limits
 ws=current_workspace()
 try:
  if preview_mode():raise ValueError('This preview can save your plan, but cannot activate external publishing.')
  if not app_control.allowed(workspace_id=ws):raise ValueError('Activate a supported subscription before scheduling these posts.')
  if ws!='owner':
   with db() as identity:verified=identity.execute('SELECT email_verified FROM customer_accounts WHERE workspace_id=?',(ws,)).fetchone()
   if not verified or not verified['email_verified']:raise ValueError('Verify your email in Account settings before activating this schedule.')
  if app_control.publishing_paused(ws):raise ValueError('Publishing is paused. Resume it in publishing settings before scheduling.')
  if request.form.get('confirmed')!='yes':raise ValueError('Review the seven captions, Page and dates before confirming.')
  identifier=int(request.form.get('plan_id','0'));page_id=request.form.get('page_id','');meta_accounts.get_page_token(ws,page_id)
  plan_limits.validate_destinations(ws,[('facebook_page',None,page_id)])
  start=datetime.strptime(request.form.get('start_date',''),'%Y-%m-%d').date();hour=datetime.strptime(request.form.get('local_time',''),'%H:%M').time();zone=ZoneInfo(request.form.get('time_zone','UTC'))
  due=[]
  for n in range(7):
   date=start+timedelta(days=n);local=datetime.combine(date,hour,tzinfo=zone);at=local.astimezone(timezone.utc)
   if at.astimezone(zone).replace(tzinfo=None)!=local.replace(tzinfo=None):raise ValueError('That local time is skipped by a daylight-saving change. Choose a different time.')
   if at<=datetime.now(timezone.utc) or at>datetime.now(timezone.utc)+timedelta(days=366):raise ValueError('Choose a future first posting time within the next year.')
   due.append(at.isoformat(timespec='seconds'))
  with db() as c:
   c.execute('BEGIN IMMEDIATE')
   plan=c.execute('SELECT id FROM business_launch_plans WHERE id=? AND workspace_id=?',(identifier,ws)).fetchone()
   if not plan:raise ValueError('Save your own plan to this account first.')
   rows=c.execute('SELECT p.*,m.day FROM business_launch_posts m JOIN app_posts p ON p.id=m.post_id WHERE m.plan_id=? AND p.workspace_id=? ORDER BY m.day',(identifier,ws)).fetchall()
   if len(rows)!=7:raise ValueError('This plan no longer has seven drafts. Review the saved posts before scheduling.')
   if all(r['platform']=='facebook_page' and r['page_id']==page_id and r['status']=='scheduled' and r['due_at']==due[i] for i,r in enumerate(rows)):
    return jsonify(message='This seven-post schedule is already saved. No duplicate posts were created.',scheduled=7)
   if any(r['platform']!='draft' or r['status']!='draft_ready' for r in rows):raise ValueError('Some of these posts have already been scheduled or changed. Manage them individually in Your posts.')
   for at in due:plan_limits.check_schedule(ws,[('facebook_page',None,page_id)],at,exclude_post_ids=[r['id'] for r in rows])
   for i,r in enumerate(rows):c.execute("UPDATE app_posts SET platform='facebook_page',page_id=?,due_at=?,status='scheduled' WHERE id=? AND workspace_id=?",(page_id,due[i],r['id'],ws))
  return jsonify(message='Seven posts are scheduled for the selected Page. You can cancel them or pause publishing in Settings.',scheduled=7)
 except (ValueError,TypeError,PermissionError,KeyError) as exc:return jsonify(error=str(exc) or 'Check the selected Page and posting time.'),400
