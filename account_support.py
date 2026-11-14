"""Account self-service; reset secrets stay in server-side mail delivery only."""
from __future__ import annotations
import io,ipaddress,json,os,re,secrets,smtplib,sqlite3,ssl,time
import requests
from email.message import EmailMessage
from urllib.parse import urlparse
from flask import Blueprint,abort,current_app,flash,redirect,render_template,request,send_file,session
import customer_accounts

account_support_bp=Blueprint('account_support',__name__)
_MESSAGE='Request processed. For a matching account, check your inbox and spam folder for reset instructions. If nothing arrives, try again later or contact support.'
_EMAIL=re.compile(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+\Z')


def _mail_config():
    origin=os.getenv('AUTOPOST_PUBLIC_URL','').rstrip('/')
    parsed=urlparse(origin)
    try:
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None,443):
            return None
        if parsed.hostname.lower()=='localhost' or parsed.hostname.endswith(('.local','.internal')):
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:return None
        except ValueError:
            pass
        host=os.getenv('AUTOPOST_SMTP_HOST','').strip();sender=os.getenv('AUTOPOST_SMTP_FROM','').strip()
        port=int(os.getenv('AUTOPOST_SMTP_PORT','587'));security=os.getenv('AUTOPOST_SMTP_SECURITY','starttls')
        user=os.getenv('AUTOPOST_SMTP_USER','');password=os.getenv('AUTOPOST_SMTP_PASSWORD','')
        if not host or any(c in host for c in '\r\n') or not _EMAIL.fullmatch(sender) or not 1<=port<=65535 or security not in ('starttls','ssl'):
            return None
        if bool(user)!=bool(password):return None
        return dict(origin=origin,host=host,port=port,sender=sender,security=security,user=user,password=password)
    except (ValueError,TypeError):
        return None


def mail_configured():
    return _mail_config() is not None


def _send_account_email(email,subject,text):
    config=_mail_config()
    if not config:raise RuntimeError('Account email is not configured.')
    message=EmailMessage();message['From']=config['sender'];message['To']=email;message['Subject']=subject
    message.set_content(text)
    context=ssl.create_default_context()
    if config['security']=='ssl':
        transport=smtplib.SMTP_SSL(config['host'],config['port'],timeout=15,context=context)
    else:
        transport=smtplib.SMTP(config['host'],config['port'],timeout=15)
    with transport as smtp:
        smtp.ehlo()
        if config['security']=='starttls':smtp.starttls(context=context);smtp.ehlo()
        if config['user']:smtp.login(config['user'],config['password'])
        smtp.send_message(message)


def _deliver_reset(email,token):
    config=_mail_config()
    if not config:raise RuntimeError('Recovery email is not configured.')
    _send_account_email(email,'Reset your Auto Poster AI password','Someone requested a password reset for your Auto Poster AI account. Open this link within 30 minutes:\n\n'+config['origin']+'/account/reset#token='+token+'\n\nIf you did not request this, ignore this message. Your password has not changed.')


def _deliver_verification(email,token):
    config=_mail_config()
    if not config:raise RuntimeError('Account confirmation email is not configured.')
    _send_account_email(email,'Confirm your Auto Poster AI email','Confirm this email for your Auto Poster AI account. Open this link within 30 minutes:\n\n'+config['origin']+'/account/verify#token='+token+'\n\nIf you did not request this, ignore this message. This email never changes your password or starts publishing.')


def _csrf_public():
    if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(32)
    if request.method=='POST':
        value=request.form.get('_csrf','')
        if not isinstance(value,str) or not secrets.compare_digest(value.encode(),str(session['csrf']).encode()):abort(403)
    return session['csrf']


def _customer():
    from app_mvp import require_login,current_workspace
    require_login();ws=current_workspace()
    user=customer_accounts.get(session.get('account_id'))
    if not user or ws=='owner':abort(403,'This page manages customer accounts. The owner account uses service settings.')
    return user


@account_support_bp.after_request
def private_response(response):
    response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer'
    return response


@account_support_bp.route('/account/forgot',methods=['GET','POST'])
def forgot_password():
    csrf=_csrf_public();notice='';error='';status=200
    if request.method=='POST':
        if not mail_configured():
            error='Password recovery email is not available on this service yet. If you are signed in, you can change your password in Account settings.';status=503
        else:
            try:
                email=request.form.get('email','').strip().lower()
                if not _EMAIL.fullmatch(email) or len(email)>254:raise ValueError('Enter a valid email address.')
                customer_accounts.attempt('recovery-ip',request.remote_addr or 'local',limit=8,window=600)
                customer_accounts.attempt('recovery-email',email,limit=3,window=3600)
                result=customer_accounts.create_reset_token(email)
                if result:
                    try:_deliver_reset(result['email'],result['token'])
                    except (smtplib.SMTPException,OSError,ValueError):
                        # No recipient, token or upstream credentials in application logs.
                        current_app.logger.warning('A recovery email delivery failed.')
                notice=_MESSAGE
            except PermissionError as exc:error=str(exc);status=429
            except ValueError as exc:error=str(exc);status=400
    return render_template('account_support.html',mode='forgot',csrf=csrf,notice=notice,error=error,token='',mail_ready=mail_configured()),status


@account_support_bp.route('/account/reset',methods=['GET','POST'])
def reset_password():
    csrf=_csrf_public();error='';token=request.form.get('token','')
    if request.method=='POST':
        try:
            customer_accounts.attempt('reset-ip',request.remote_addr or 'local',limit=12,window=600)
            customer_accounts.reset_password(token,request.form.get('password',''))
            session.clear();flash('Your password was changed. Sign in with your new password.');return redirect('/account?mode=login')
        except (ValueError,PermissionError) as exc:
            error=str(exc)
    return render_template('account_support.html',mode='reset',csrf=csrf,notice='',error=error,token=token,mail_ready=mail_configured()),400 if error else 200


@account_support_bp.route('/account/verify',methods=['GET','POST'])
def verify_email():
    csrf=_csrf_public();error='';notice='';token=request.form.get('token','')
    if request.method=='POST':
        try:
            customer_accounts.attempt('verification-confirm-ip',request.remote_addr or 'local',limit=12,window=600)
            customer_accounts.verify_email(token)
            notice='Your email is confirmed. You can continue to your workspace; publishing still requires your explicit approval.'
        except (ValueError,PermissionError) as exc:error=str(exc)
    return render_template('account_support.html',mode='verify',csrf=csrf,notice=notice,error=error,token=token,mail_ready=mail_configured()),400 if error else 200


@account_support_bp.route('/account/send-verification',methods=['POST'])
def send_verification():
    user=_customer()
    if user['email_verified']:
        flash('Your email is already confirmed.');return redirect('/account/settings')
    if not mail_configured():
        flash('Account confirmation email is not configured on this service yet. Your drafts remain available.','error');return redirect('/account/settings')
    try:
        customer_accounts.attempt('verification-user',user['id'],limit=3,window=600)
        customer_accounts.attempt('verification-ip',request.remote_addr or 'local',limit=3,window=600)
        result=customer_accounts.create_verification_token(user['id'])
        if result:_deliver_verification(result['email'],result['token'])
        flash('Confirmation email requested. Check your inbox and spam folder. The link lasts 30 minutes.')
    except PermissionError as exc:flash(str(exc),'error')
    except (smtplib.SMTPException,OSError,ValueError):
        current_app.logger.warning('An account confirmation email delivery failed.')
        flash('We could not deliver confirmation email. Try again later; your drafts remain available.','error')
    return redirect('/account/settings')


@account_support_bp.route('/account/settings')
def account_settings():
    user=_customer()
    return render_template('account_settings.html',user=user,csrf=session['csrf'],mail_ready=mail_configured())


@account_support_bp.route('/account/change-password',methods=['POST'])
def change_password():
    user=_customer()
    try:
        customer_accounts.attempt('password-change',user['id'],limit=8,window=600)
        customer_accounts.change_password(user['id'],request.form.get('current_password',''),request.form.get('password',''))
    except (ValueError,PermissionError) as exc:flash(str(exc),'error');return redirect('/account/settings')
    session.clear();flash('Your password was changed. Sign in again on your devices.');return redirect('/account?mode=login')


_EXPORT_TABLES={
 'app_posts':('id','content','platform','due_at','status','remote_id','created_at','frequency','page_id'),
 'app_series':('id','content','platform','frequency','time_zone','active','page_id'),
 'creative_profiles':('name','tone','location','hashtags','instructions'),
 'creative_automations':('id','name','source_kind','source_url','source_text','instructions','page_ids_json','approval','cadence_seconds','status','created'),
 'imported_guest_setups':('id','payload_json','imported_at'),
 'connected_facebook_pages':('page_id','name','connected_at'),
 'creative_suggestions':('id','text','provider','created'),
 'creative_working_drafts':('content','revision','saved_at'),
 'business_launch_plans':('id','brief_json','created'),
 'analytics_reports':('id','text','provider','model','created_at'),
 'website_requests':('id','url','topic','cadence','status','created_at'),
 'subscriptions':('status','plan','current_period_end'),
 'clip_jobs':('id','status','created','updated','source_duration','options_json','summary_json','error','caption_revision'),
}


def export_customer(user):
    ws=user['workspace_id'];data={'account':{k:user[k] for k in ('name','email','created_at')},'exported_at':int(time.time()),'records':{}}
    with customer_accounts.connect() as c:
        existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table,fields in _EXPORT_TABLES.items():
            if table not in existing:continue
            columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
            if 'workspace_id' not in columns:continue
            chosen=[f for f in fields if f in columns]
            if chosen:data['records'][table]=[dict(r) for r in c.execute('SELECT '+','.join(chosen)+' FROM '+table+' WHERE workspace_id=?',(ws,))]
    return data


@account_support_bp.route('/account/export',methods=['POST'])
def account_export():
    user=_customer();data=io.BytesIO(json.dumps(export_customer(user),ensure_ascii=False,indent=2).encode())
    return send_file(data,mimetype='application/json',as_attachment=True,download_name='auto-poster-account-export.json')


def _checkout_clearance(user):
    """Read-only provider check. A local timer is not proof a checkout was unpaid."""
    ws=user['workspace_id']
    with customer_accounts.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'billing_checkout_attempts' not in tables:return None
        attempt=c.execute('SELECT * FROM billing_checkout_attempts WHERE workspace_id=?',(ws,)).fetchone()
        if not attempt:return None
        attempt=dict(attempt)
        sub=c.execute('SELECT * FROM subscriptions WHERE workspace_id=?',(ws,)).fetchone() if 'subscriptions' in tables else None
        if sub and sub['status'] not in ('canceled','cancelled','incomplete_expired'):
            raise ValueError('Cancel your subscription in Billing and wait for cancellation confirmation before deleting your account.')
    identifier=attempt.get('session_id');key=os.getenv('STRIPE_SECRET_KEY','')
    error='We could not confirm that your checkout is closed. Open Billing or contact support before deleting your account.'
    if not key or not isinstance(identifier,str) or not re.fullmatch(r'cs_[A-Za-z0-9_]{1,250}',identifier):raise ValueError(error)
    try:
        response=requests.get('https://api.stripe.com/v1/checkout/sessions/'+identifier,headers={'Authorization':'Bearer '+key},timeout=(5,10),allow_redirects=False)
        if response.status_code!=200:raise ValueError(error)
        data=response.json()
        if not isinstance(data,dict) or data.get('id')!=identifier or data.get('client_reference_id')!=ws:raise ValueError(error)
        subscription=data.get('subscription')
        if isinstance(subscription,dict):subscription=subscription.get('id')
        closed_unpaid=data.get('status')=='expired' and data.get('payment_status')=='unpaid' and not subscription
        closed_subscription=bool(sub and sub['status'] in ('canceled','cancelled','incomplete_expired') and subscription==sub['subscription_id'])
        if not (closed_unpaid or closed_subscription):raise ValueError(error)
    except (requests.RequestException,TypeError,ValueError):
        raise ValueError(error) from None
    return (attempt['idempotency_key'],identifier)


def delete_customer(user,password,confirmation):
    if confirmation!='DELETE':raise ValueError('Type DELETE to confirm account removal.')
    if not customer_accounts.verify_password(user['id'],password):raise ValueError('Your current password did not match.')
    clearance=_checkout_clearance(user)
    ws=user['workspace_id'];now=int(time.time());clip_identifiers=[]
    with customer_accounts.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT auth_version FROM customer_accounts WHERE id=? AND workspace_id=?',(user['id'],ws)).fetchone()
        if not current or current['auth_version']!=user['auth_version']:
            raise ValueError('Your account changed during this request. Sign in again before deleting.')
        existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'clip_jobs' in existing:
            clips=c.execute('SELECT id,status FROM clip_jobs WHERE workspace_id=?',(ws,)).fetchall()
            if any(r['status'] in ('uploading','queued','rendering') for r in clips):
                raise ValueError('Wait for video renders to finish, or delete queued clips in Clip Studio, before deleting your account.')
            clip_identifiers=[r['id'] for r in clips]
        if 'subscriptions' in existing:
            sub=c.execute('SELECT status FROM subscriptions WHERE workspace_id=?',(ws,)).fetchone()
            if sub and sub['status'] not in ('canceled','cancelled','incomplete_expired'):
                raise ValueError('Cancel your subscription in Billing and wait for cancellation confirmation before deleting your account.')
        if 'billing_checkout_attempts' in existing:
            pending=c.execute('SELECT idempotency_key,session_id FROM billing_checkout_attempts WHERE workspace_id=?',(ws,)).fetchone()
            if pending and clearance!=(pending['idempotency_key'],pending['session_id']):
                raise ValueError('A checkout changed while deletion was being checked. Review Billing before trying again.')
        if 'billing_checkout_sessions' in existing:
            cols={r[1] for r in c.execute('PRAGMA table_info(billing_checkout_sessions)')}
            if 'workspace_id' in cols:
                rows=c.execute('SELECT * FROM billing_checkout_sessions WHERE workspace_id=?',(ws,)).fetchall()
                for row in rows:
                    if 'status' not in cols or row['status'] not in ('expired','complete','completed','cancelled','canceled'):
                        raise ValueError('A checkout is still awaiting confirmation. Finish or close it before deleting your account.')
        if 'app_posts' in existing and c.execute("SELECT 1 FROM app_posts WHERE workspace_id=? AND status='sending'",(ws,)).fetchone():
            raise ValueError('A post is currently being delivered. Wait for its result before deleting your account.')
        if 'creative_automations' in existing:
            columns={r[1] for r in c.execute('PRAGMA table_info(creative_automations)')}
            if 'lease_until' in columns and c.execute('SELECT 1 FROM creative_automations WHERE workspace_id=? AND lease_until>?',(ws,now)).fetchone():
                raise ValueError('An automation is finishing its current check. Pause it and try deletion again in a few minutes.')
        # Retain only the signed payment-state record, without account profile or content.
        retained={'subscriptions','billing_checkout_sessions','billing_checkout_intents','billing_events'}
        for table in sorted(existing):
            if table.startswith('sqlite_') or table in retained or table=='customer_accounts':continue
            # Table names come from SQLite itself, never user input.
            quoted='"'+table.replace('"','""')+'"'
            columns={r[1] for r in c.execute('PRAGMA table_info('+quoted+')')}
            if 'workspace_id' in columns:c.execute('DELETE FROM '+quoted+' WHERE workspace_id=?',(ws,))
        c.execute('DELETE FROM customer_password_resets WHERE user_id=?',(user['id'],))
        c.execute('DELETE FROM customer_email_verifications WHERE user_id=?',(user['id'],))
        c.execute('DELETE FROM customer_accounts WHERE id=? AND workspace_id=?',(user['id'],ws))
    # File cleanup follows the committed account/workspace deletion. Pending
    # jobs were excluded under the same SQLite write lock as their deletion.
    if clip_identifiers:
        import shutil
        from clip_studio_web import job_dir
        for identifier in clip_identifiers:
            shutil.rmtree(job_dir(identifier),ignore_errors=True)
    return True


@account_support_bp.route('/account/delete',methods=['POST'])
def account_delete():
    user=_customer()
    try:
        customer_accounts.attempt('delete-account',user['id'],limit=5,window=600)
        delete_customer(user,request.form.get('current_password',''),request.form.get('confirmation',''))
    except (ValueError,PermissionError) as exc:flash(str(exc),'error');return redirect('/account/settings')
    session.clear();return render_template('account_support.html',mode='deleted',notice='Your account and app content were removed. Existing posts on social platforms were not deleted. Signed billing records may be retained for payment support.',error='',csrf='',token='',mail_ready=False)
