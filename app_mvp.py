"""Testable, single-owner Auto Poster AI web app.

Run with: pip install flask requests python-dotenv && python -m app_mvp
Requires AUTOPOST_APP_PASSWORD. No social password collection.
This MVP is deliberately not a public multi-tenant SaaS or App Store binary.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from werkzeug.exceptions import Unauthorized
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, render_template_string, request, session, url_for, jsonify, Response, send_from_directory

import app_control
import meta_accounts
import automation_setup
import public_setup
import customer_accounts
import plan_limits

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("AUTOPOST_WEB_SECRET") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=64*1024)
DATABASE = os.getenv("AUTOPOST_WEB_DB", "autopost_web.db")
PASSWORD = os.getenv("AUTOPOST_APP_PASSWORD")
LOCK = threading.RLock()
MAX_POSTS_PER_RUN = 10
_MAINTENANCE_LOCK = threading.Lock()
_MAINTENANCE_THREADS = {}
_MAINTENANCE_LAST = {}


def _same_secret(a, b):
    return isinstance(a,str) and isinstance(b,str) and hmac.compare_digest(a.encode("utf-8"),b.encode("utf-8"))


def preview_mode() -> bool:
    return os.getenv('AUTOPOST_PREVIEW_MODE', 'false').lower() == 'true'


def db():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS app_posts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          content TEXT NOT NULL, platform TEXT NOT NULL,
          due_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'scheduled',
          remote_id TEXT, error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS app_series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, platform TEXT NOT NULL,
            frequency TEXT NOT NULL CHECK(frequency IN ('daily','weekly')),
            time_zone TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""")
        columns = {row[1] for row in c.execute("PRAGMA table_info(app_posts)")}
        if "series_id" not in columns:
            c.execute("ALTER TABLE app_posts ADD COLUMN series_id INTEGER")
        if "website_url" not in columns:
            c.execute("ALTER TABLE app_posts ADD COLUMN website_url TEXT")
        if "frequency" not in columns:
            c.execute("ALTER TABLE app_posts ADD COLUMN frequency TEXT NOT NULL DEFAULT 'once'")
        if "page_id" not in columns:
            c.execute("ALTER TABLE app_posts ADD COLUMN page_id TEXT")
        series_columns = {row[1] for row in c.execute("PRAGMA table_info(app_series)")}
        if "page_id" not in series_columns:
            c.execute("ALTER TABLE app_series ADD COLUMN page_id TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_series_due ON app_posts(series_id,due_at)")
        c.execute("""CREATE TABLE IF NOT EXISTS website_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            topic TEXT,
            cadence TEXT NOT NULL CHECK(cadence IN ('new','daily')),
            status TEXT NOT NULL DEFAULT 'needs_integration',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        for table in ("app_posts", "app_series", "website_requests"):
            names = {row[1] for row in c.execute("PRAGMA table_info("+table+")")}
            if "workspace_id" not in names:
                c.execute("ALTER TABLE "+table+" ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'owner'")
            c.execute("CREATE INDEX IF NOT EXISTS "+table+"_workspace ON "+table+"(workspace_id)")
    customer_accounts.init()
    app_control.init()
    meta_accounts.init()
    automation_setup.init()
    public_setup.init()
    plan_limits.init()
    import creative_jobs, editor_connectors
    creative_jobs.init()
    editor_connectors.init()
    import creative, analytics_workbench
    creative.init()
    analytics_workbench.init()
    import launch
    launch.init()


def current_workspace():
    """Resolve workspace from authenticated identity, never a submitted identifier."""
    if session.get("authorized") is not True:
        abort(401)
    if session.get("account_id"):
        account = customer_accounts.get(session["account_id"])
        if not account or int(session.get("auth_version",0)) != int(account.get("auth_version",0)):
            session.clear()
            abort(401)
        return account["workspace_id"]
    if not PASSWORD:
        abort(503, "Owner access is not configured")
    return "owner"


def require_login():
    current_workspace()


def is_owner():
    return current_workspace() == "owner"


@app.context_processor
def account_context():
    user=customer_accounts.get(session.get("account_id")) if session.get("authorized") else None
    if user and int(session.get("auth_version",0)) != int(user.get("auth_version",0)):
        user=None
    return {"customer_account":user,"owner_workspace":session.get("authorized") is True and not session.get("account_id"),"workspace_id":user["workspace_id"] if user else "owner"}


def valid_due(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timezone required")
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Provide a date/time including an explicit timezone") from exc


def publish_to_facebook(text):
    if not app_control.allowed():
        raise PermissionError("Subscription inactive: publishing is paused")
    # Reuse the existing official Graph API Page publisher.
    from core.facebook import FacebookClient
    return "meta:" + FacebookClient.from_env().publish_message(text)


def claim_due(now=None, *, drafts_only=False):
    """Claim one due post before any outbound call. Unknown responses stay on hold."""
    now = now or datetime.now(timezone.utc)
    with LOCK:
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            filters = ("AND (platform='draft' OR NOT EXISTS(SELECT 1 FROM publishing_pauses p "
                       "WHERE p.workspace_id=app_posts.workspace_id AND p.paused=1)) "
                       "AND (platform='draft' OR workspace_id='owner' OR EXISTS(SELECT 1 FROM customer_accounts a WHERE a.workspace_id=app_posts.workspace_id AND a.email_verified=1)) ")
            args = [now.isoformat(timespec="seconds")]
            if drafts_only:
                filters += "AND platform='draft' "
            elif app_control.require_payment():
                filters += ("AND (platform='draft' OR EXISTS(SELECT 1 FROM subscriptions s "
                            "WHERE s.workspace_id=app_posts.workspace_id AND s.status='active' "
                            "AND s.current_period_end>?)) ")
                args.append(int(now.timestamp()))
            row = c.execute(
                "SELECT * FROM app_posts WHERE status='scheduled' AND due_at<=? "
                + filters + "ORDER BY CASE WHEN platform='draft' THEN 1 ELSE 0 END, due_at, id LIMIT 1", args,
            ).fetchone()
            if row:
                c.execute("UPDATE app_posts SET status='sending' WHERE id=?", (row["id"],))
            return dict(row) if row else None


def next_recurrence_due(last_due, frequency, zone_name, now):
    """Preserve local wall-clock hour and skip missed occurrences after outages."""
    tz = ZoneInfo(zone_name)
    current = now.astimezone(tz)
    previous = datetime.fromisoformat(last_due).astimezone(tz)
    day_step = timedelta(days=1 if frequency == "daily" else 7)
    next_day = previous.date() + day_step
    while True:
        candidate = datetime.combine(next_day, previous.timetz().replace(tzinfo=None), tzinfo=tz)
        if candidate > current:
            return candidate.astimezone(timezone.utc).isoformat(timespec="seconds")
        next_day += day_step


def run_due(now=None):
    """Run due jobs only while the server confirms an active entitlement."""
    handled = 0
    now = now or datetime.now(timezone.utc)
    for _ in range(MAX_POSTS_PER_RUN):
        job = claim_due(now)
        if job is None:
            break
        handled += 1
        state, remote, error = "needs_review", None, None
        if not app_control.allowed(workspace_id=job["workspace_id"]) and job["platform"] != "draft":
            with db() as c:
                c.execute("UPDATE app_posts SET status='scheduled' WHERE id=? AND status='sending'", (job["id"],))
            break
        if job["platform"] != "draft" and app_control.publishing_paused(job["workspace_id"]):
            with db() as c:
                c.execute("UPDATE app_posts SET status='scheduled' WHERE id=? AND status='sending'",(job["id"],))
            continue
        try:
            if preview_mode() and job["platform"] != "draft":
                state = "needs_connection"
            elif job["platform"] == "facebook" and job["workspace_id"] == "owner":
                plan_limits.reserve_delivery(job["workspace_id"],job["id"],job["platform"],job["page_id"],now=now)
                remote = publish_to_facebook(job["content"])
                state = "published"
            elif job["platform"] == "facebook_page":
                plan_limits.reserve_delivery(job["workspace_id"],job["id"],job["platform"],job["page_id"],now=now)
                remote = meta_accounts.publish_message(
                    job["workspace_id"], job["page_id"], job["content"]
                )
                state = "published"
            elif job["platform"] == "draft":
                state = "draft_ready"
            else:
                state = "needs_connection"
        except plan_limits.PlanLimitError as exc:
            error=str(exc)[:300]
            state="quota_held"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            state = "needs_review"
        with db() as c:
            c.execute(
                "UPDATE app_posts SET status=?,remote_id=?,error=? "
                "WHERE id=? AND status='sending'",
                (state, remote, error, job["id"]),
            )
            if state in ("published", "draft_ready") and job.get("series_id"):
                series = c.execute(
                    "SELECT * FROM app_series WHERE id=? AND workspace_id=? AND active=1", (job["series_id"],job["workspace_id"])
                ).fetchone()
                if series:
                    next_due = next_recurrence_due(
                        job["due_at"], series["frequency"], series["time_zone"], now
                    )
                    c.execute(
                        "INSERT OR IGNORE INTO app_posts"
                        "(content,platform,due_at,status,series_id,frequency,page_id,workspace_id)"
                        " VALUES(?,?,?,'scheduled',?,?,?,?)",
                        (series["content"], series["platform"], next_due,
                         series["id"], series["frequency"], series["page_id"], job["workspace_id"]),
                    )
    return handled


def recover_interrupted():
    with db() as c:
        c.execute(
            "UPDATE app_posts SET status='needs_review', "
            "error='Worker interrupted; check destination before retry' WHERE status='sending'"
        )


def _maintenance_pass(name, callback, interval):
    """Keep slow collection/report APIs from blocking the delivery scheduler."""
    with _MAINTENANCE_LOCK:
        active=_MAINTENANCE_THREADS.get(name)
        moment=time.monotonic()
        if active and active.is_alive() or moment-_MAINTENANCE_LAST.get(name,-interval)<interval:
            return
        def run():
            try:
                callback()
            except Exception:
                app.logger.exception("%s maintenance pass failed",name)
        thread=threading.Thread(target=run,daemon=True,name='autopost-'+name)
        _MAINTENANCE_THREADS[name]=thread
        _MAINTENANCE_LAST[name]=moment
        thread.start()


def worker():
    while True:
        try:
            run_due()
        except Exception:
            app.logger.exception("Scheduler pass failed")
        import creative_jobs, analytics_workbench
        _maintenance_pass('creative',creative_jobs.tick,20)
        _maintenance_pass('analytics',analytics_workbench.tick,60)
        time.sleep(20)


PAGE = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI · Post</title><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Auto Poster AI"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#101319">
<style>
:root{color-scheme:dark;font:16px system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:#101319;color:#eff3f9}
header,main{max-width:900px;margin:auto;padding:16px}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #303744}
header b{font-size:19px}a{color:#c8fa89}
h1{font-size:30px;letter-spacing:-1px;margin:4px 0 8px}
h2{font-size:17px;margin:0 0 12px}
p,small{color:#b2bdca;line-height:1.5}
.panel{background:#1c232e;border:1px solid #343e4d;border-radius:17px;padding:20px;margin:18px 0}
.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:16px}
label{display:block;font-weight:650;font-size:13px;margin:15px 0 7px}
textarea,input,select,button{font:inherit}
textarea,input,select{width:100%;padding:13px;border-radius:10px;border:1px solid #465267;color:#f2f5f9;background:#111722}
textarea{resize:vertical;min-height:135px}
button{padding:12px 15px;background:#c7f78b;border:0;border-radius:10px;color:#18251a;font-weight:750;cursor:pointer}
button.ghost{background:#303b49;color:#e5edf6}
.pill{display:inline-flex;align-items:center;padding:6px 10px;border-radius:100px;background:#324052;color:#e5eef6;font-size:12px;font-weight:600}
.success{background:#284737;color:#c7f78b}.pending{background:#514231;color:#ffe0a0}
.note{padding:12px;border:1px solid #4a5569;background:#26303d;border-radius:10px;font-size:12px;color:#c7d1df;line-height:1.6}
.dest{display:flex;gap:12px;align-items:center;cursor:pointer;padding:12px 9px;border-bottom:1px solid #344050;margin:0}
.dest input{width:18px;height:18px;flex-shrink:0;accent-color:#c7f78b}
.dest span{flex:1}.dest small{display:block;font-size:11px}.dest b{font-size:13px}
.dest .pill{font-size:10px;padding:4px 7px}
.dest:last-child{border:0}
details{margin:10px 0;border:1px solid #455369;border-radius:12px}
summary{cursor:pointer;padding:13px 15px;font-weight:700;color:#d1e7bd}
details>div{padding:0 12px 10px}
.row{display:flex;gap:9px;flex-wrap:wrap}
.post{border-top:1px solid #384253;padding:12px 0}
.post:first-child{border:0}
.post p{white-space:pre-wrap;overflow-wrap:anywhere;color:#f2f5f9}
.status{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.website{display:flex;align-items:center;gap:8px;margin:9px 0}
.website input{flex:1;min-width:0}.website button{flex-shrink:0}
.warning{color:#ffbdbd}
@media(max-width:700px){.grid{grid-template-columns:1fr}header,main{padding:15px}.panel{padding:17px}h1{font-size:27px}}
</style></head><body>
<header><b>✳ Auto Poster AI</b><div class="row"><a href="/pages">Facebook Pages</a><a href="/control">⚙ Controls</a><a href="/logout">Sign out</a></div></header>
<main>
<p><span class="pill">Private beta · Single-owner dashboard · Not ready for public signup</span></p>
{% if payment_required and not paid %}<p class="note">⏸ Your subscription is not active. Posting and scheduling are disabled until payment is confirmed. <a href="/control">Manage subscription</a>.</p>{% endif %}
<h1>Make a post</h1>
<p>Write your message, choose where it goes, then post now or schedule it.</p>
<div class="grid">
<section class="panel">
<form method="post" action="/schedule" id="composer">
<input type="hidden" name="_csrf" value="{{csrf}}">
<label for="content">Your post</label>
<textarea id="content" name="content" required maxlength="10000"
placeholder="Write about your latest video, a sale, an adoption, a community update, or anything you want to share."></textarea>
<details id="destinations" open>
<summary>Step 1 · Choose where to post <span id="selected-count" class="pill">0 selected</span></summary>
<div>
<label class="dest"><input type="checkbox" name="destinations" value="draft">
<span><b>Save a draft</b><small>No external publishing</small></span><span class="pill success">Ready</span></label>
<label class="dest"><input type="checkbox" name="destinations" value="facebook">
<span><b>Existing owner Facebook Page</b><small>Only available when owner credentials are configured</small></span>
<span class="pill {{ 'success' if fb_enabled else 'pending' }}">{{ 'Owner Page' if fb_enabled else 'Connect first' }}</span></label>
{% for pg in fb_pages %}
<label class="dest"><input type="checkbox" name="facebook_pages" value="{{pg.page_id}}">
<span><b>Facebook · {{pg.name}}</b><small>Post directly to this authorized Page</small></span><span class="pill success">Ready</span></label>
{% endfor %}
<p><small><a href="/pages">Connect a Facebook business Page</a>. Choose each destination separately; none is selected automatically.</small></p>
{% for app in other_apps %}
<label class="dest"><input type="checkbox" name="destinations" value="{{app}}">
<span><b>{{app|title}}</b><small>Not connected — request only, no publishing</small></span><span class="pill pending">Needs integration</span></label>
{% endfor %}
<div id="websites"></div>
<button type="button" class="ghost" id="add-website">＋ Post to my website</button>
<p><small>A website needs its own publishing connection. Adding a URL here saves a request; it doesn't publish.</small></p>
</div>
</details>
<details>
<summary>Advanced: repeat this exact post (optional)</summary>
<div>
<label for="frequency">Repeat this identical message?</label>
<select name="frequency" id="frequency">
<option value="once">No, post only once</option>
<option value="daily">Yes, every day (same text)</option>
<option value="weekly">Yes, every week (same text)</option>
</select>
<p><small>This repeats the <b>same message</b>. For new updates from a site, use Website Autopilot below instead.</small></p>
</div>
</details>
<label for="when">Step 2 · When should this post be processed?</label>
<select name="when" id="when"><option value="now">As soon as possible</option>
<option value="later">Choose date & time</option></select>
<div id="later-fields" hidden>
<label for="due">Choose your local time</label><input type="datetime-local" id="due" name="due">
</div>
<input type="hidden" id="utc_due" name="utc_due">
<input type="hidden" id="time_zone" name="time_zone" value="UTC">
<p class="note">Only connected Facebook Pages can publish. Drafts stay in this app. Other destinations create requests only; they will not publish. Confirm each destination before continuing.</p>
<button type="submit" style="width:100%;margin-top:15px">Review and confirm destinations</button>
</form>
</section>
<aside>
<div class="panel"><h2>Your activity</h2>
<p><b>{{counts.get('scheduled',0)}}</b> scheduled ·
<b>{{counts.get('published',0)}}</b> published</p>
<p><b>{{counts.get('needs_connection',0)}}</b> awaiting connection ·
<b>{{counts.get('needs_review',0)}}</b> needing review</p>
<div class="note">Private beta: free hosting may sleep, and local drafts may disappear after restart. Do not rely on this preview for time-critical publishing.</div></div>
<div class="panel"><h2>Want posts from a website?</h2>
<p>For example, find new adoption listings, your own blog updates, or permitted community feeds.</p>
<p><b>Watch a website</b> is different from publishing <b>to</b> a website.</p>
<a href="/website">＋ Set up Website Autopilot</a>
<p><small>Setup request only until the website connector is available.</small></p>
</div>
</aside>
</div>
<section class="panel"><h2>Your posts</h2>
{% for p in posts %}
<div class="post">
<div class="status"><b>{{'Post to website' if p.platform == 'website' else p.platform|title}}</b><span class="pill {{'success' if p.status=='published' else 'pending' if p.status=='needs_connection' else ''}}">{{p.status.replace('_',' ')|title}}</span><small>{{p.frequency}}</small></div>
{% if p.website_url %}<small>{{p.website_url}}</small>{% endif %}
{% if p.page_id %}<small> · Facebook Page: {{page_names.get(p.page_id,p.page_id)}}</small>{% endif %}
<p>{{p.content}}</p>
<small>{{p.due_at}}</small>
{% if p.remote_id %}<small> · Confirmed ID {{p.remote_id}}</small>{% endif %}
{% if p.error %}<p class="warning">{{p.error}}</p>{% endif %}
{% if p.status == 'scheduled' %}
<form method="post" action="/cancel/{{p.id}}">
<input type="hidden" name="_csrf" value="{{csrf}}">
<button type="submit" class="ghost">{{'Stop repeating' if p.series_id else 'Cancel post'}}</button></form>
{% endif %}
</div>
{% else %}<p>No posts yet. Create your first one above.</p>{% endfor %}
</section>
</main>
<script>
const form=document.getElementById('composer'),when=document.getElementById('when');
const later=document.getElementById('later-fields'),date=document.getElementById('due');
const websites=document.getElementById('websites');
when.addEventListener('change',()=>{later.hidden=when.value!=='later';date.required=when.value==='later'});
document.getElementById('add-website').addEventListener('click',()=>{
if(websites.children.length>=5){alert('You can add up to five websites at once.');return}
const row=document.createElement('div');row.className='website';
const input=document.createElement('input');input.name='websites';input.type='url';
input.placeholder='https://your-wordpress-or-blog.com';input.required=true;input.setAttribute('aria-label','Website to publish to');
const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent='Remove';
remove.addEventListener('click',()=>{row.remove();updateCount()});
row.append(input,remove);websites.append(row);updateCount();
});
function updateCount(){
const n=form.querySelectorAll('input[name="destinations"]:checked,input[name="facebook_pages"]:checked').length+websites.children.length;
document.getElementById('selected-count').textContent=n+' selected';
}
form.querySelectorAll('input[name="destinations"],input[name="facebook_pages"]').forEach(el=>el.addEventListener('change',updateCount));
form.addEventListener('submit',e=>{
if(!form.querySelector('input[name="destinations"]:checked,input[name="facebook_pages"]:checked') && !websites.children.length){
e.preventDefault();alert('Choose where this post should go.');return;
}
if(when.value==='later'){
const d=new Date(date.value);
if(!date.value||isNaN(d.getTime())){e.preventDefault();alert('Choose a valid date.');return}
document.getElementById('utc_due').value=d.toISOString();
}
document.getElementById('time_zone').value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
const chosen=[...form.querySelectorAll('input[name="destinations"]:checked,input[name="facebook_pages"]:checked')].map(input=>{
  const label=input.closest('label');
  return label?.querySelector('b')?.textContent?.trim()||input.value;
});
form.querySelectorAll('input[name="websites"]').forEach(input=>chosen.push('Website request: '+input.value));
const timing=when.value==='later'?new Date(date.value).toLocaleString():'As soon as the service is available';
const requestOnly=chosen.some(name=>/instagram|tiktok|youtube|linkedin|pinterest|threads|website request|^x$/i.test(name));
const message='Confirm destination'+(chosen.length===1?'':'s')+':\n• '+chosen.join('\n• ')+
  '\n\nWhen: '+timing+'\nRepeat: '+document.getElementById('frequency').value+
  (requestOnly?'\nSome destinations only save a connection request; they will NOT publish.':'')+
  '\n\nContinue?';
if(!window.confirm(message)) e.preventDefault();
});
</script></body></html>
"""

LOGIN = """<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI</title>
<link rel="icon" href="/icon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png"><link rel="stylesheet" href="/static/brand/brand.css"><meta name="theme-color" content="#0b1424">
<style>body{background:#101218;color:white;font:16px system-ui;max-width:370px;margin:15vh auto;padding:20px}
input,button{box-sizing:border-box;width:100%;padding:15px;margin:10px 0;border-radius:10px;font:inherit}
input{background:#222834;color:white;border:1px solid #444}
button{background:#beee83;border:0;color:#122007;font-weight:700}
p{color:#b1b9c9}</style><h1 class="brand-login-logo"><img class="brand-icon" src="/static/brand/mark.svg" width="48" height="48" alt="">Auto Poster AI</h1><p>Sign in to your private starter dashboard.</p>
<form method="post" action="/login"><input name="password" type="password"
autocomplete="current-password" placeholder="App password" required>
<button>Sign in</button></form></html>"""


WEBSITE_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI · Website Autopilot</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{background:#101319;color:#f1f3f8;max-width:620px;margin:auto;padding:22px}
h1{font-size:27px}p,small{color:#b9c3d1;line-height:1.55}
section{background:#1d2530;border:1px solid #3c4655;border-radius:16px;padding:20px;margin:18px 0}
label{display:block;font-weight:650;font-size:14px;margin:14px 0 7px}
input,select,button{font:inherit;border-radius:9px;padding:12px}
input,select{display:block;width:100%;background:#101722;color:white;border:1px solid #4c5769}
button{background:#c7f78b;border:0;font-weight:750;color:#172217;cursor:pointer}
a{color:#c7f78b}.tag{display:inline-block;padding:4px 9px;border-radius:40px;background:#46505f;font-size:12px}
.item{padding:13px 0;border-top:1px solid #42505e;overflow-wrap:anywhere}
</style></head><body><p><a href="/">← Back to Post</a></p>
<h1>Watch a website</h1>
{% if preview_mode %}
<p style="padding:13px;border:1px solid #856b3e;border-radius:12px;background:#393429"><b>Plan your source before subscribing.</b> Saving a URL records a future workflow request only. This preview will not monitor, scrape or publish from websites.</p>
{% else %}
<p>Tell Auto Poster AI what source you want to monitor. This is for <b>new content</b>, not repeating the same message.</p>{% endif %}
<section>
<h2>New website source</h2>
<form method="post" action="/website">
<fieldset style="border:0;padding:0;margin:0">
<input type="hidden" name="_csrf" value="{{csrf}}">
<label for="url">Website or feed URL</label>
<input id="url" name="url" type="url" required placeholder="https://example.org/adoptions">
<label for="topic">What are you interested in?</label>
<input id="topic" name="topic" maxlength="120" placeholder="New animal adoption listings, blog posts…">
<label for="cadence">When should new items be considered?</label>
<select id="cadence" name="cadence">
<option value="new">Whenever something new appears</option>
<option value="daily">Once-daily roundup</option>
</select>
<p><small>Only use sources you have permission to reuse. New-source verification,
duplicate checks, and connection approval will be required before publishing.</small></p>
<button type="submit">Save website plan</button></fieldset></form></section>
<section><h2>Website requests</h2>
{% for r in requests %}
<div class="item"><b>{{r.url}}</b><p>{{r.topic or 'All approved updates'}} · {{'Whenever new content appears' if r.cadence=='new' else 'Daily summary'}}</p>
<span class="tag">Not connected — no monitoring yet</span></div>
{% else %}<p>You haven't added a source yet.</p>{% endfor %}
</section></body></html>"""


def validate_website_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(url) > 2048:
        raise ValueError("Use a complete HTTPS website address without credentials")
    if parsed.hostname.lower() in ("localhost",) or parsed.hostname.endswith((".local", ".internal")):
        raise ValueError("Use a public website address")
    return url


@app.route("/website", methods=["GET", "POST"])
def website_requests():
    if request.method == "POST":
        # A URL submission only saves setup intent. No fetching or publishing.
        url = request.form.get("url", "").strip()
        topic = request.form.get("topic", "").strip()
        cadence = request.form.get("cadence", "new")
        try:
            validate_website_url(url)
        except ValueError as exc:
            abort(400, str(exc))
        if len(topic) > 120 or cadence not in ("new", "daily"):
            abort(400, "Invalid website monitoring settings")
        with db() as c:
            c.execute("INSERT INTO website_requests(url,topic,cadence,workspace_id) VALUES(?,?,?,?)", (url, topic, cadence,current_workspace()))
        return redirect("/website")
    with db() as c:
        rows = c.execute("SELECT * FROM website_requests WHERE workspace_id=? ORDER BY id DESC LIMIT 50",(current_workspace(),)).fetchall()
    return render_template_string(WEBSITE_PAGE, requests=rows, csrf=session["csrf"],
                                  preview_mode=preview_mode())


@app.route("/welcome")
def public_website():
    """Public marketing site; the paid owner dashboard stays separate."""
    return send_from_directory("website", "index.html")


@app.route("/start", methods=["GET"])
def public_onboarding():
    """Password-free, no-payment setup. Nothing is persisted server-side here."""
    return render_template("public_onboarding.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        abort(503, "Configure AUTOPOST_APP_PASSWORD on the server first.")
    if request.method == "POST":
        try:
            customer_accounts.attempt("owner-login",request.remote_addr or "local",limit=30)
        except PermissionError as exc:
            abort(429,str(exc))
        provided = request.form.get("password", "")
        if _same_secret(provided, PASSWORD):
            session.clear()
            session["authorized"] = True
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect("/")
        abort(401, "Wrong password")
    return LOGIN


ACCOUNT_DESTINATIONS={"setup":"/?finish_setup=1","launch":"/launch","pages":"/pages","studio":"/studio","connections":"/connections","reports":"/reports","settings":"/account/settings"}


@app.route("/account", methods=["GET", "POST"])
def account():
    if request.method=="GET" and session.get("authorized") is True:
        try:current_workspace()
        except Unauthorized:pass
        else:return redirect(ACCOUNT_DESTINATIONS.get(request.args.get('next'),'/'))
    mode=request.form.get("mode",request.args.get("mode","register"))
    if mode not in ("register","login"):
        abort(400)
    if not session.get("csrf"):
        session["csrf"]=secrets.token_urlsafe(32)
    next_value=request.values.get("next","") if request.values.get("next") in ACCOUNT_DESTINATIONS else ""
    error="";status=200
    email=request.form.get("email","")[:254];name=request.form.get("name","")[:80]
    if request.method=="POST":
        if not _same_secret(request.form.get("_csrf",""),session["csrf"]):
            abort(403,"Refresh the account form and try again.")
        try:
            customer_accounts.attempt('account:'+mode,request.remote_addr or 'local')
            if mode=="register":
                user=customer_accounts.register(email,request.form.get("password",""),name)
            else:
                user=customer_accounts.login(email,request.form.get("password",""))
                if not user:
                    raise ValueError("We couldn't sign you in. Check your email and password.")
            session.clear()
            session.update(authorized=True,account_id=user["id"],auth_version=user.get("auth_version",0),csrf=secrets.token_urlsafe(32))
            return redirect(ACCOUNT_DESTINATIONS.get(next_value,"/"))
        except PermissionError as exc:
            error=str(exc);status=429
        except ValueError as exc:
            error=str(exc);status=400
    return render_template("account.html",mode=mode,error=error,email=email,name=name,next_value=next_value,csrf=session["csrf"]),status


@app.route("/logout")
def logout():
    customer=bool(session.get("account_id"))
    session.clear()
    return redirect("/account?mode=login" if customer else "/login")


@app.before_request
def protect():
    if request.endpoint in ("login", "account", "static", "stripe_webhook", "manifest", "app_icon", "legacy_claim", "public_website", "public_onboarding", "launch.begin", "launch.build", "launch.export_plan", "account_support.forgot_password", "account_support.reset_password", "account_support.verify_email", "help.guide", "help.privacy", "help.terms"):
        return
    if (request.endpoint == "home" and request.method == "GET"
            and os.getenv("AUTOPOST_PUBLIC_LANDING", "false").lower() == "true"
            and session.get("authorized") is not True):
        return redirect("/welcome")
    try:require_login()
    except Unauthorized:
        if request.method=='GET' and 'text/html' in request.headers.get('Accept','').lower() and not request.path.startswith('/api/'):
            destination=next((name for name in ('pages','studio','connections','reports') if request.path=='/'+name or request.path.startswith('/'+name+'/')),'settings' if request.path.startswith('/account/') or request.path=='/control' else '')
            return redirect('/account?mode=login'+('&next='+destination if destination else ''))
        raise
    if request.method == "POST":
        # Browser form requests must include a session CSRF token.
        if not _same_secret(
            request.headers.get("X-CSRF-Token") or request.form.get("_csrf", ""), session.get("csrf", "missing")
        ):
            abort(403)


    if session.get('account_id') and request.endpoint in ('facebook_connect','facebook_activate','start_checkout','activate_saved_post','creative.canva_connect'):
        customer=customer_accounts.get(session['account_id'])
        if not customer or not customer.get('email_verified'):
            flash('Verify your email in Account settings before connecting accounts or activating paid publishing.')
            return redirect('/account/settings')


@app.route("/start/import", methods=["POST"])
def import_guest_setup():
    """Owner-only import of browser setup; no posts or workers get scheduled."""
    raw_text = request.form.get("setup_payload", "")
    if not raw_text or len(raw_text) > 15000:
        abort(400, "Choose a valid saved setup")
    import json
    try:
        payload = json.loads(raw_text)
        public_setup.import_setup(payload,workspace_id=current_workspace())
    except (ValueError, TypeError) as exc:
        abort(400, str(exc))
    return redirect("/?imported=1")


@app.route("/")
def home():
    with db() as c:
        rows = c.execute("SELECT * FROM app_posts WHERE workspace_id=? ORDER BY id DESC LIMIT 100",(current_workspace(),)).fetchall()
        counts = {r["status"]: r["n"] for r in c.execute(
            "SELECT status,COUNT(*) n FROM app_posts WHERE workspace_id=? GROUP BY status",(current_workspace(),)
        )}
    pages = meta_accounts.list_pages(current_workspace())
    return render_template(
        "dashboard.html", posts=rows, counts=counts, csrf=session["csrf"],
        fb_pages=pages, page_names={p["page_id"]: p["name"] for p in pages},
        fb_enabled=is_owner() and bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")),
        other_apps=("instagram", "tiktok", "youtube", "linkedin", "pinterest", "threads", "x"),
        payment_required=app_control.require_payment(), paid=app_control.allowed(workspace_id=current_workspace()),
        preview_mode=preview_mode(), settings=app_control.get_settings(workspace_id=current_workspace()),
        publishing_paused=app_control.publishing_paused(current_workspace()),
        plans=automation_setup.list_plans(workspace_id=current_workspace()),
        imported_setups=public_setup.list_setups(workspace_id=current_workspace()),
        imported=(request.args.get("imported") == "1"),
    )


SUPPORTED_DESTINATIONS = {"draft", "facebook", "instagram", "tiktok", "youtube",
                          "linkedin", "pinterest", "threads", "x"}


def selected_destinations(form):
    """Keep unsupported destinations on hold instead of implying API access."""
    platforms = list(dict.fromkeys(form.getlist("destinations")))
    if any(platform not in SUPPORTED_DESTINATIONS for platform in platforms):
        raise ValueError("Unknown destination")
    websites = list(dict.fromkeys(url.strip() for url in form.getlist("websites") if url.strip()))
    if len(websites) > 5:
        raise ValueError("Choose at most five websites")
    for url in websites:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or len(url) > 2048):
            raise ValueError("Website URLs must use HTTPS and cannot contain credentials")
    page_ids = list(dict.fromkeys(form.getlist("facebook_pages")))
    connected = {p["page_id"] for p in meta_accounts.list_pages(current_workspace())
                 if p["can_publish"]}
    if any(page_id not in connected for page_id in page_ids):
        raise ValueError("Choose only Facebook Pages you have connected")
    result = [(p, None, None) for p in platforms]
    result += [("facebook_page", None, pid) for pid in page_ids]
    result += [("website", url, None) for url in websites]
    if not result or len(result) > 12:
        raise ValueError("Add at least one app or website")
    return result


@app.route("/schedule", methods=["POST"])
def schedule():
    if not is_owner():
        try:
            customer_accounts.attempt('schedule',current_workspace(),limit=60,window=300)
        except PermissionError as exc:
            abort(429,str(exc))
    with db() as c:
        pending=c.execute("SELECT count(*) FROM app_posts WHERE workspace_id=? AND platform='draft' AND status='scheduled'",(current_workspace(),)).fetchone()[0]
    if pending>=100:
        abort(429,"You have 100 drafts waiting to be processed. Let them finish before adding more.")
    # Building plans and drafts is free. Live delivery requires server-side
    # entitlement AND explicit subsequent activation after setup.
    # Setup-only posts never auto-activate on a payment webhook.
    content = request.form.get("content", "").strip()
    if not content or len(content) > 10000:
        abort(400, "Write a post before scheduling")
    try:
        targets = selected_destinations(request.form)
    except ValueError as exc:
        abort(400, str(exc))
    frequency = request.form.get("frequency", "once")
    if frequency not in ("once", "daily", "weekly"):
        abort(400, "Invalid posting frequency")
    zone_name = request.form.get("time_zone", "UTC")
    try:
        ZoneInfo(zone_name)
    except (KeyError, ValueError):
        abort(400, "Invalid timezone")
    when = request.form.get("when", "later")
    if when not in ("now", "later"):
        abort(400, "Choose now or a future date")
    try:
        due = (datetime.now(timezone.utc).isoformat(timespec="seconds")
               if when == "now" else valid_due(request.form.get("utc_due")))
    except ValueError as exc:
        abort(400, str(exc))
    facebook_ready = is_owner() and bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN"))
    try:
        plan_limits.validate_destinations(current_workspace(),targets)
        plan_limits.check_schedule(current_workspace(),targets,due)
    except plan_limits.PlanLimitError as exc:
        abort(exc.status,str(exc))
    with db() as c:
        for platform, website_url, page_id in targets:
            supported = (platform == "draft"
                         or (platform == "facebook" and facebook_ready)
                         or platform == "facebook_page")
            # A setup draft never becomes runnable merely because someone pays
            # or an integration is later connected. Activation is a separate step.
            if platform == "draft":
                status = "scheduled"
            elif preview_mode() or not app_control.allowed(workspace_id=current_workspace()):
                status = "setup_only"
            else:
                status = "scheduled" if supported else "needs_connection"
            series_id = None
            if frequency != "once" and (supported or status == "setup_only"):
                cursor = c.execute(
                    "INSERT INTO app_series(content,platform,frequency,time_zone,page_id,workspace_id) VALUES(?,?,?,?,?,?)",
                    (content, platform, frequency, zone_name, page_id,current_workspace()),
                )
                series_id = cursor.lastrowid
            c.execute(
                "INSERT INTO app_posts"
                "(content,platform,due_at,status,series_id,frequency,website_url,page_id,workspace_id)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (content, platform, due, status, series_id, frequency, website_url,page_id,current_workspace()),
            )
    return redirect("/")


@app.route("/automations", methods=["GET", "POST"])
def automations():
    if not is_owner():
        if request.method=='POST':flash('Use the creative studio to save and start your automation.')
        return redirect('/studio#automations')

    """Pre-pay planning area; no automation setup route starts a worker."""
    if request.method == "POST":
        try:
            chosen_id = request.form.get("plan_id", "").strip()
            plan_id = int(chosen_id) if chosen_id else None
            automation_setup.save(request.form, workspace_id=current_workspace(),plan_id=plan_id)
        except (ValueError, TypeError) as exc:
            abort(400, str(exc))
        return redirect("/automations")
    plans = automation_setup.list_plans(workspace_id=current_workspace())
    chosen = request.args.get("edit", "", type=str)
    editing = next((p for p in plans if str(p["id"]) == chosen), None)
    pages = meta_accounts.list_pages(current_workspace())
    return render_template(
        "automations.html", csrf=session["csrf"], plans=plans, editing=editing,
        pages=pages, preview_mode=preview_mode(), paid=app_control.allowed(workspace_id=current_workspace()),
        kinds=automation_setup.KINDS,
    )


@app.route("/automations/<int:plan_id>/delete", methods=["POST"])
def automations_delete(plan_id):
    automation_setup.delete(plan_id,workspace_id=current_workspace())
    return redirect("/automations")


@app.route("/activate/<int:post_id>", methods=["POST"])
def activate_saved_post(post_id):
    """A deliberate post-setup action, not an automatic consequence of payment."""
    if preview_mode():
        abort(403, "Publishing is disabled on the free preview")
    if not app_control.allowed(workspace_id=current_workspace()):
        abort(402, "Subscribe before activating external publishing")
    now = datetime.now(timezone.utc)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM app_posts WHERE id=? AND workspace_id=?", (post_id,current_workspace())).fetchone()
        if not row or row["status"] != "setup_only":
            abort(404, "No unpublished setup plan found")
        if row["platform"] == "facebook":
            if not is_owner() or not (os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")):
                abort(409, "Connect the Facebook Page before activation")
        elif row["platform"] == "facebook_page":
            try:
                meta_accounts.get_page_token(current_workspace(), row["page_id"])
            except (PermissionError, ValueError):
                abort(409, "Reconnect the selected Page before activation")
        else:
            abort(409, "This platform cannot publish yet")
        # A plan created for a time already past should not surprise-publish
        # the moment payment succeeds. User must choose a fresh future time.
        due = datetime.fromisoformat(row["due_at"])
        if due <= now:
            abort(409, "This posting time has passed. Create a new schedule to activate.")
        try:
            plan_limits.check_schedule(current_workspace(),[(row["platform"],row["website_url"],row["page_id"])],row["due_at"],exclude_post_ids=[post_id])
        except plan_limits.PlanLimitError as exc:
            abort(exc.status,str(exc))
        c.execute("UPDATE app_posts SET status='scheduled' WHERE id=? AND workspace_id=? AND status='setup_only'",
                  (post_id,current_workspace()))
    return redirect("/#posts")


@app.route("/cancel/<int:post_id>", methods=["POST"])
def cancel(post_id):
    workspace_id=current_workspace()
    with db() as c:
        post=c.execute("SELECT series_id FROM app_posts WHERE id=? AND workspace_id=?",(post_id,workspace_id)).fetchone()
        if not post:
            abort(404,"Post not found")
        if post["series_id"]:
            c.execute("UPDATE app_series SET active=0 WHERE id=? AND workspace_id=?",(post["series_id"],workspace_id))
            c.execute("UPDATE app_posts SET status='cancelled' WHERE series_id=? AND workspace_id=? AND status IN ('scheduled','setup_only')",(post["series_id"],workspace_id))
        else:
            c.execute("UPDATE app_posts SET status='cancelled' WHERE id=? AND workspace_id=? AND status IN ('scheduled','setup_only')",(post_id,workspace_id))
    return redirect("/")




PAGES_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI · Facebook Pages</title>
<style>
:root{color-scheme:dark;font:16px -apple-system,system-ui,sans-serif}
*{box-sizing:border-box}body{background:#101319;color:#f1f6fc;margin:0}
main{max-width:720px;margin:auto;padding:20px 17px 60px}
a{color:#c8f98a}h1{font-size:30px;letter-spacing:-1px}
p,small{color:#aabbca;line-height:1.5}
.card{background:#1c2835;border:1px solid #344758;padding:20px;border-radius:18px;margin:15px 0}
.item{padding:14px 0;border-bottom:1px solid #3b4858;display:flex;align-items:center;gap:12px}
.item:last-child{border:0}.item span{flex:1}.item small{display:block;font-size:12px}
button,.action{font:inherit;display:inline-block;border:0;border-radius:11px;background:#c8f98a;color:#142018;padding:12px 18px;font-weight:750;cursor:pointer;text-decoration:none}
button.remove{background:#374659;color:#f9e8e8;padding:10px}
input[type=checkbox]{width:21px;height:21px;accent-color:#c8f98a}
.tag{background:#394b59;padding:5px 9px;border-radius:35px;font-size:12px}
</style></head><body><main>
<a href="/">← Back to posts</a>
<h1>Your Facebook Pages</h1>
<p>Sign in with Facebook once, select the business Pages you manage, and then choose the exact destination when creating a post. You do not need Meta developer keys.</p>
<section class="card"><h2>Connect pages</h2>
{% if configured %}
<a class="action" href="/facebook/connect">Connect with Facebook</a>
{% else %}
<p><b>Connection needs setup by Auto Poster AI.</b> The service owner must configure the Meta app and callback before this button works. Customers will not configure developer keys.</p>
{% endif %}
<p><small>Facebook grants access only to eligible Pages. Some Pages may need additional permission or to be reauthorized. Connection alone never starts automatic posting.</small></p></section>
{% if pending %}
<section class="card"><h2>Choose where Auto Poster AI may publish</h2>{% if basic_limits.basic %}<p>Basic includes one Facebook Page. Choose one below. To switch Pages later, disconnect the current Page first.</p>{% endif %}
<form method="post" action="/facebook/activate"><input type="hidden" name="_csrf" value="{{csrf}}">
{% for p in pending %}
<div class="item"><input type="{{'radio' if basic_limits.basic else 'checkbox'}}" id="p{{p.page_id}}" name="page_ids" value="{{p.page_id}}" {{'disabled' if not p.can_publish}}>
<label for="p{{p.page_id}}"><b>{{p.name}}</b><small>{{'Eligible to publish' if p.can_publish else 'No permission to publish on this Page'}}</small></label></div>
{% endfor %}
<button type="submit">Connect selected Pages</button>
</form></section>
{% endif %}
<section class="card"><h2>Connected destinations</h2>
{% for p in pages %}
<div class="item"><span><b>{{p.name}}</b><small>Facebook Page · ID {{p.page_id}}</small></span>
<form method="post" action="/facebook/disconnect/{{p.page_id}}">
<input type="hidden" name="_csrf" value="{{csrf}}">
<button class="remove" type="submit">Disconnect</button></form></div>
{% else %}<p>No Facebook business Pages connected through sign-in yet.</p>{% endfor %}
</section>
{% if owner_workspace %}<section class="card"><h2>Already using a Page token?</h2>
<p>Your existing single-owner Facebook publishing setup is separate and remains unchanged. This sign-in feature is the future customer path, not a replacement of your working Page credentials.</p>
</section>{% endif %}
</main></body></html>
"""


@app.route("/pages")
def pages_overview():
    return render_template_string(
        PAGES_PAGE, csrf=session["csrf"], owner_workspace=is_owner(), basic_limits=plan_limits.limits(current_workspace()), configured=(meta_accounts.configured() and not preview_mode()),
        pending=meta_accounts.pending_choices(current_workspace()),
        pages=meta_accounts.list_pages(current_workspace()),
    )


@app.route("/facebook/connect")
def facebook_connect():
    if preview_mode():
        abort(403, "Facebook authorization is unavailable in free preview mode")
    if not meta_accounts.configured():
        abort(503, "Facebook business login is not configured on this server")
    state = secrets.token_urlsafe(40)
    session["fb_oauth_state"] = state
    session["fb_oauth_started"] = int(time.time())
    session["fb_oauth_workspace"] = current_workspace()
    return redirect(meta_accounts.login_url(state), code=302)


@app.route("/facebook/callback")
def facebook_callback():
    oauth_workspace=session.pop("fb_oauth_workspace",None)
    if oauth_workspace!=current_workspace():
        abort(403,"Facebook connection belongs to another workspace")
    expected = session.pop("fb_oauth_state", None)
    started = session.pop("fb_oauth_started", 0)
    supplied = request.args.get("state", "")
    if (not expected or not _same_secret(expected, supplied)
            or int(time.time()) - int(started) > 600):
        abort(403, "Invalid or expired Facebook connection")
    if request.args.get("error"):
        return redirect("/pages")
    code = request.args.get("code", "")
    try:
        available = meta_accounts.discover_pages(code)
        meta_accounts.remember_pending(current_workspace(), available)
    except (RuntimeError, ValueError) as exc:
        app.logger.warning("Facebook connection failed: %s", str(exc))
        abort(502, "Unable to retrieve Facebook Pages. Please try reconnecting.")
    return redirect("/pages")


@app.route("/facebook/activate", methods=["POST"])
def facebook_activate():
    page_ids = request.form.getlist("page_ids")
    try:
        plan_limits.validate_pages(current_workspace(),page_ids)
        meta_accounts.activate_pages(current_workspace(), page_ids)
    except plan_limits.PlanLimitError as exc:
        abort(exc.status,str(exc))
    except (ValueError, PermissionError) as exc:
        abort(400, str(exc))
    return redirect("/pages")


@app.route("/facebook/disconnect/<page_id>", methods=["POST"])
def facebook_disconnect(page_id):
    try:
        removed = meta_accounts.disconnect(current_workspace(), page_id)
    except ValueError as exc:
        abort(400, str(exc))
    if removed:
        with db() as c:
            c.execute("""
                UPDATE app_posts SET status='needs_connection'
                WHERE platform='facebook_page' AND page_id=? AND workspace_id=?
                  AND status='scheduled'
            """, (page_id,current_workspace()))
            c.execute("""
                UPDATE app_series SET active=0
                WHERE platform='facebook_page' AND page_id=? AND workspace_id=?
            """, (page_id,current_workspace()))
    return redirect("/pages")


CONTROL_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#101319"><link rel="manifest" href="/manifest.webmanifest">
<title>Auto Poster AI · Control Center</title>
<style>
:root{color-scheme:dark;font:16px -apple-system,BlinkMacSystemFont,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#101319;color:#f3f7fa}
main{max-width:700px;margin:auto;padding:20px 16px 80px}
a{color:#bff88a}h1{letter-spacing:-.8px}h2{font-size:19px}
p,small{color:#adbcc9;line-height:1.5}
.card{background:#1e2835;padding:20px;border-radius:20px;border:1px solid #344455;margin:16px 0}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.row label{flex:1}.item{padding:12px 0;border-bottom:1px solid #3a4756}
.item:last-child{border:0}.item small{display:block}
input[type=checkbox]{height:23px;width:23px;accent-color:#c8f98a}
input[type=number],select{width:100%;padding:14px;background:#111923;color:white;border:1px solid #536274;border-radius:10px;margin:7px 0 12px;font:inherit}
button{font:inherit;background:#c8f98a;border:0;color:#1b2c18;padding:15px;border-radius:12px;font-weight:750;width:100%;cursor:pointer}
button.secondary{background:#2f4050;color:white}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pill{font-size:12px;padding:6px 10px;border-radius:50px;background:#324458;color:#dbe9f7}
.warn{background:#4c332e;color:#ffdfc3}
@media(max-width:390px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<p><a href="/">← Back to composer</a></p>
<h1>Publishing and subscription</h1>
{% for message in get_flashed_messages() %}<p role="status">{{message}}</p>{% endfor %}
<section class="card"><div class="row"><h2>Your publishing</h2><span class="pill">{{'Paused' if publishing_paused else 'Ready'}}</span></div>
<p>{{'Scheduled posts and automations stay saved, but new external publishing is paused.' if publishing_paused else 'Your approved schedules can run when their connections and subscription are ready.'}} A request already in flight may finish.</p>
<form method="post" action="/publishing/pause"><input type="hidden" name="_csrf" value="{{csrf}}"><input type="hidden" name="paused" value="{{'0' if publishing_paused else '1'}}"><button>{{'Resume publishing' if publishing_paused else 'Pause all publishing'}}</button></form></section>
{% if preview_mode %}
<p class="warn" style="padding:12px;border-radius:10px">Free test site: these settings are for exploring only. They do not control your live Facebook publisher.</p>
{% else %}
<p>Manage your publishing and subscription here. {% if owner_workspace %}Legacy source controls apply once your GitHub Actions bridge is connected.{% endif %}</p>{% endif %}
<section class="card">
<div class="row"><h2>Subscription</h2><span class="pill {{' ' if paid else 'warn'}}">{{'Active' if paid else 'Inactive'}}</span></div>
{% if preview_mode %}<p><b>Free beta preview</b> · No billing or checkout here.</p>
<p>The commercial subscription service is not connected yet. A payment cannot be made from this test site.</p>
{% else %}<p><b>{{billing.plan|title}}</b></p>
<p>{{billing.status.replace('_',' ')|title}}{% if billing.current_period_end %} · Paid access until {{expires}} UTC{% endif %}</p>
{% if not has_subscription %}
{% if checkout_configured %}<form method="post" action="/billing/checkout">
<input name="_csrf" type="hidden" value="{{csrf}}"><label for="plan">Choose a subscription</label><select id="plan" name="plan">{% for name,item in billing_plans.items() if item.configured %}<option value="{{name}}">{{item.label}}</option>{% endfor %}</select>
<button type="submit">See price and activate subscription</button></form>{% else %}<p>Subscription activation is not available on this service yet. Your saved work stays available.</p>{% endif %}
{% else %}
<form method="post" action="/billing/portal">
<input name="_csrf" type="hidden" value="{{csrf}}">
<button class="secondary" type="submit">Manage or cancel subscription</button></form>
{% endif %}
<small>You review the exact price and renewal terms in checkout before confirming. Subscribing never activates a saved post automatically.</small>{% endif %}
</section>
{% if owner_workspace %}<section class="card">
<h2>Legacy owner source controls</h2>
{% if preview_mode %}<p>These specialized rules are shown for development testing only. They are not linked to your existing Facebook Page.</p>{% endif %}
<details><summary>Show advanced source controls</summary>
{% if not paid and payment_required %}<p>Settings can be saved, but the service will not publish while unpaid.</p>{% endif %}
<form method="post" action="/control">
<input type="hidden" name="_csrf" value="{{csrf}}">
<div class="row item"><label for="enabled"><b>Legacy source runner</b><small>Allow or pause the separate connected GitHub source workflow</small></label>
<input id="enabled" name="enabled" type="checkbox" value="1" {{'checked' if settings.enabled}}></div>
{% for name,label,sub in [
('booking','Booking photos','Existing Allen County records'),
('warrant','Wanted notices','Only verified sources'),
('case','Case updates','Only verified case sources'),
('status','Custody checks','Updates to already-published records')] %}
<div class="row item"><label for="{{name}}_enabled"><b>{{label}}</b><small>{{sub}}</small></label>
<input id="{{name}}_enabled" name="{{name}}_enabled" type="checkbox" value="1" {{'checked' if settings[name + '_enabled']}}></div>
{% endfor %}
<div class="row item"><label for="release_enabled"><b>Release announcements</b><small>Use only when the source and status are verified</small></label>
<input id="release_enabled" name="release_enabled" type="checkbox" value="1" {{'checked' if settings.release_enabled}}></div>
<div class="grid">
<div><label for="booking_interval_minutes">Booking check every (minutes)</label><input id="booking_interval_minutes" name="booking_interval_minutes" type="number" min="15" max="10080" step="15" value="{{settings.booking_interval_minutes}}"></div>
<div><label for="max_posts_per_run">Posts attempted per run</label><input id="max_posts_per_run" name="max_posts_per_run" type="number" min="1" max="10" value="{{settings.max_posts_per_run}}"></div>
<div><label for="batch_size">Photos per booking batch (0=individual)</label><input id="batch_size" name="batch_size" type="number" min="0" max="10" value="{{settings.batch_size}}"></div>
<div><label for="status_interval_minutes">Status check every (minutes)</label><input id="status_interval_minutes" name="status_interval_minutes" type="number" min="15" max="10080" step="15" value="{{settings.status_interval_minutes}}"></div>
<div><label for="warrant_interval_minutes">Wanted check every (minutes)</label><input id="warrant_interval_minutes" name="warrant_interval_minutes" type="number" min="15" max="10080" step="15" value="{{settings.warrant_interval_minutes}}"></div>
<div><label for="case_interval_minutes">Case check every (minutes)</label><input id="case_interval_minutes" name="case_interval_minutes" type="number" min="15" max="10080" step="15" value="{{settings.case_interval_minutes}}"></div>
</div>
<button type="submit">Save my settings</button>
</form></details>
</section>
<section class="card"><h2>Connection status</h2>
<p>Existing Facebook Page: <b>{{'Credential configured' if facebook_ready else 'Not connected'}}</b></p>
<p>GitHub Actions control bridge: <b>{{'Server token configured' if bridge_ready else 'Not configured'}}</b></p>
<small>A token configured on the server does not prove that the GitHub workflow has been connected. Until deployment and wiring are complete, changing these controls does not change the existing GitHub scheduled runs.</small>
</section>{% endif %}
</main></body></html>
"""


@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({"id": "/", "name": "Auto Poster AI", "short_name": "Auto Poster",
                    "start_url": "/", "display": "standalone",
                    "background_color": "#0b1424", "theme_color": "#0b1424",
                    "icons": [
                        {"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                        {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                        {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
                    ]})


@app.route("/icon.svg")
def app_icon():
    return send_from_directory("static/brand", "app-icon.svg", mimetype="image/svg+xml")



@app.route("/control", methods=["GET", "POST"])
def control_center():
    if request.method == "POST" and not is_owner():
        flash('Use the publishing pause button to control your workspace.')
        return redirect('/control')
    if request.method == "POST":
        fields = {k: request.form.get(k, 0) for k in app_control.DEFAULTS}
        try:
            app_control.update_settings(fields,workspace_id=current_workspace())
        except ValueError as exc:
            abort(400, str(exc))
        return redirect("/control")
    billing = app_control.subscription_details(workspace_id=current_workspace())
    expires = (datetime.fromtimestamp(billing["current_period_end"], timezone.utc).date()
               if billing["current_period_end"] else None)
    return render_template_string(
        CONTROL_PAGE, csrf=session["csrf"], settings=app_control.get_settings(workspace_id=current_workspace()),
        billing=billing, expires=expires,
        paid=billing["active"], payment_required=app_control.require_payment(),
        facebook_ready=is_owner() and bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")),
        bridge_ready=is_owner() and bool(os.getenv("AUTOPOST_WORKER_TOKEN")),
        preview_mode=preview_mode(),owner_workspace=is_owner(),
        publishing_paused=app_control.publishing_paused(current_workspace()),
        billing_plans=app_control.billing_plans(),checkout_configured=_checkout_ready(),
        public_sales_ready=_public_sales_ready(),has_subscription=billing["status"] not in ("not_subscribed","canceled","incomplete_expired"),
    )


@app.route("/publishing/pause",methods=["POST"])
def pause_publishing():
    choice=request.form.get('paused')
    if choice not in ('0','1'):abort(400,'Choose pause or resume.')
    app_control.set_publishing_paused(current_workspace(),choice=='1')
    flash('Publishing paused. Your drafts and schedules are saved.' if choice=='1' else 'Publishing resumed. Existing approved schedules can run.')
    return redirect('/control')


@app.route("/internal/legacy/claim", methods=["POST"])
def legacy_claim():
    required = os.getenv("AUTOPOST_WORKER_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    if len(required) < 32 or not _same_secret(supplied, "Bearer " + required):
        abort(401)
    data = request.get_json(silent=True) or {}
    result = app_control.claim_run(str(data.get("mode", "")))
    return jsonify(result)


def _public_sales_ready():
    import service_readiness
    state=service_readiness.configured()
    return state.get('public_sales_enabled') is True and state.get('configuration_complete') is True


def _checkout_ready():
    if not is_owner() and not _public_sales_ready():return False
    try:
        app_control.public_origin()
        return bool(os.getenv('STRIPE_SECRET_KEY')) and any(p['configured'] for p in app_control.billing_plans().values())
    except ValueError:
        return False


@app.route("/billing/checkout", methods=["POST"])
def start_checkout():
    if preview_mode():
        abort(403, "Checkout is disabled in free preview mode")
    workspace=current_workspace()
    if not is_owner() and not _public_sales_ready():
        abort(409,'Subscription activation is not available on this service yet. Your drafts are safe.')
    with app_control.connect() as c:
        existing=c.execute("SELECT * FROM subscriptions WHERE workspace_id=?",(workspace,)).fetchone()
    if existing and existing['status'] not in ('canceled','incomplete_expired'):
        flash('You already have a subscription record. Manage it below instead of opening a second subscription.')
        return redirect('/control')
    import stripe
    try:
        origin=app_control.public_origin()
        if not os.getenv('STRIPE_SECRET_KEY'):raise ValueError('Subscription activation is not available yet.')
        attempt=app_control.checkout_attempt(workspace,request.form.get('plan','basic'))
        if attempt.get('session_url'):return redirect(attempt['session_url'],code=303)
        stripe.api_key=os.getenv('STRIPE_SECRET_KEY')
        options=dict(mode='subscription',line_items=[{'price':attempt['price_id'],'quantity':1}],
            client_reference_id=workspace,subscription_data={'metadata':{'workspace_id':workspace,'plan':attempt['plan']}},
            success_url=origin+'/control',cancel_url=origin+'/control',expires_at=attempt['expires_at'],
            idempotency_key=attempt['idempotency_key'])
        if existing and existing['customer_id']:options['customer']=existing['customer_id']
        checkout=stripe.checkout.Session.create(**options)
        app_control.save_checkout(workspace,attempt['idempotency_key'],checkout.id,checkout.url)
        return redirect(checkout.url,code=303)
    except ValueError as exc:
        flash(str(exc));return redirect('/control')
    except stripe.StripeError:
        flash('The payment service is temporarily unavailable. Please retry this same checkout later.');return redirect('/control')


@app.route("/billing/portal", methods=["POST"])
def subscription_portal():
    if preview_mode():
        abort(403, "Billing is disabled in free preview mode")
    key = os.getenv("STRIPE_SECRET_KEY")
    try:origin=app_control.public_origin()
    except ValueError:
        flash('Subscription management is not available yet.');return redirect('/control')
    with app_control.connect() as c:
        row = c.execute(
            "SELECT customer_id FROM subscriptions WHERE workspace_id=?",
            (current_workspace(),)
        ).fetchone()
    if not row or not row["customer_id"] or not key or not origin.startswith("https://"):
        abort(503, "Billing portal is not configured")
    import stripe
    stripe.api_key = key
    try:
        portal = stripe.billing_portal.Session.create(customer=row["customer_id"], return_url=origin + "/control")
        return redirect(portal.url, code=303)
    except stripe.StripeError:
        flash('Subscription management is temporarily unavailable. Please try again later.');return redirect('/control')


@app.route("/billing/webhook", methods=["POST"])
def stripe_webhook():
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        abort(503, "Stripe webhook secret is missing")
    import stripe
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""), secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        abort(400)
    app_control.record_subscription_event(dict(event))
    return jsonify({"received": True})


from creative import creative_bp
app.register_blueprint(creative_bp)
from launch import launch_bp
app.register_blueprint(launch_bp)
from account_support import account_support_bp
app.register_blueprint(account_support_bp)
from business_help import help_bp
app.register_blueprint(help_bp)


def start():
    if not PASSWORD or len(PASSWORD) < 12:
        raise RuntimeError("Set AUTOPOST_APP_PASSWORD to a strong password of at least 12 characters")
    if not os.getenv("AUTOPOST_WEB_SECRET") or len(os.getenv("AUTOPOST_WEB_SECRET", "")) < 32:
        raise RuntimeError("Set AUTOPOST_WEB_SECRET to at least 32 unpredictable characters")
    init_db()
    recover_interrupted()
    threading.Thread(target=worker, daemon=True).start()
    app.run(host=os.getenv("AUTOPOST_WEB_HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8080")), debug=False, use_reloader=False)


if __name__ == "__main__":
    start()
