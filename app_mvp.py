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
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template_string, request, session, url_for

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("AUTOPOST_WEB_SECRET") or secrets.token_hex(32)
DATABASE = os.getenv("AUTOPOST_WEB_DB", "autopost_web.db")
PASSWORD = os.getenv("AUTOPOST_APP_PASSWORD")
LOCK = threading.RLock()
MAX_POSTS_PER_RUN = 10


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
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_series_due ON app_posts(series_id,due_at)")
        c.execute("""CREATE TABLE IF NOT EXISTS website_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            topic TEXT,
            cadence TEXT NOT NULL CHECK(cadence IN ('new','daily')),
            status TEXT NOT NULL DEFAULT 'needs_integration',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")


def require_login():
    if not PASSWORD:
        abort(503, "Set AUTOPOST_APP_PASSWORD before starting the web app.")
    if session.get("authorized") is not True:
        abort(401)


def valid_due(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timezone required")
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Provide a date/time including an explicit timezone") from exc


def publish_to_facebook(text):
    # Reuse the existing official Graph API Page publisher.
    from core.facebook import FacebookClient
    return "meta:" + FacebookClient.from_env().publish_message(text)


def claim_due(now=None):
    """Claim one due post before any outbound call. Unknown responses stay on hold."""
    now = now or datetime.now(timezone.utc)
    with LOCK:
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM app_posts WHERE status='scheduled' AND due_at<=? "
                "ORDER BY due_at, id LIMIT 1",
                (now.isoformat(timespec="seconds"),),
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
    """Run due jobs. Only confirmed successes continue recurring series."""
    handled = 0
    now = now or datetime.now(timezone.utc)
    for _ in range(MAX_POSTS_PER_RUN):
        job = claim_due(now)
        if job is None:
            break
        handled += 1
        state, remote, error = "needs_review", None, None
        try:
            if job["platform"] == "facebook":
                remote = publish_to_facebook(job["content"])
                state = "published"
            elif job["platform"] == "draft":
                state = "draft_ready"
            else:
                state = "needs_connection"
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
                    "SELECT * FROM app_series WHERE id=? AND active=1", (job["series_id"],)
                ).fetchone()
                if series:
                    next_due = next_recurrence_due(
                        job["due_at"], series["frequency"], series["time_zone"], now
                    )
                    c.execute(
                        "INSERT OR IGNORE INTO app_posts"
                        "(content,platform,due_at,status,series_id,frequency)"
                        " VALUES(?,?,?,'scheduled',?,?)",
                        (series["content"], series["platform"], next_due,
                         series["id"], series["frequency"]),
                    )
    return handled


def recover_interrupted():
    with db() as c:
        c.execute(
            "UPDATE app_posts SET status='needs_review', "
            "error='Worker interrupted; check destination before retry' WHERE status='sending'"
        )


def worker():
    while True:
        try:
            run_due()
        except Exception:
            app.logger.exception("Scheduler pass failed")
        time.sleep(20)


PAGE = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI · Post</title>
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
<header><b>✳ Auto Poster AI</b><a href="/logout">Sign out</a></header>
<main>
<p><span class="pill">Single-owner starter · Live connections shown below</span></p>
<h1>Make a post</h1>
<p>Write your message, choose where it goes, then post now or schedule it.</p>
<div class="grid">
<section class="panel">
<form method="post" action="/schedule" id="composer">
<input type="hidden" name="_csrf" value="{{csrf}}">
<label for="content">Your post</label>
<textarea id="content" name="content" required maxlength="10000"
placeholder="Write about your latest video, a sale, an adoption, a community update, or anything you want to share."></textarea>
<details id="destinations">
<summary>＋ Choose where to post <span id="selected-count" class="pill">1 selected</span></summary>
<div>
<label class="dest"><input type="checkbox" name="destinations" value="draft" checked>
<span><b>Save a draft</b><small>No external publishing</small></span><span class="pill success">Ready</span></label>
<label class="dest"><input type="checkbox" name="destinations" value="facebook">
<span><b>Facebook Page</b><small>Text posts on your authorized Page</small></span>
<span class="pill {{ 'success' if fb_enabled else 'pending' }}">{{ 'Ready' if fb_enabled else 'Connect first' }}</span></label>
{% for app in other_apps %}
<label class="dest"><input type="checkbox" name="destinations" value="{{app}}">
<span><b>{{app|title}}</b><small>Save publishing request</small></span><span class="pill pending">Needs integration</span></label>
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
<label for="when">When should the first one go?</label>
<select name="when" id="when"><option value="now">As soon as possible</option>
<option value="later">Choose date & time</option></select>
<div id="later-fields" hidden>
<label for="due">Choose your local time</label><input type="datetime-local" id="due" name="due">
</div>
<input type="hidden" id="utc_due" name="utc_due">
<input type="hidden" id="time_zone" name="time_zone" value="UTC">
<p class="note">Your connected Facebook Page is the only real social destination in this starter. Other selections are saved as <b>Needs connection</b>—nothing will be published there. You're always able to review and cancel pending posts.</p>
<button type="submit" style="width:100%;margin-top:15px">Post or schedule</button>
</form>
</section>
<aside>
<div class="panel"><h2>Your activity</h2>
<p><b>{{counts.get('scheduled',0)}}</b> scheduled ·
<b>{{counts.get('published',0)}}</b> published</p>
<p><b>{{counts.get('needs_connection',0)}}</b> awaiting connection ·
<b>{{counts.get('needs_review',0)}}</b> needing review</p>
<div class="note">The scheduler runs while the hosted service stays on. This starter still needs deployment before you can use it from any device.</div></div>
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
const n=form.querySelectorAll('input[name="destinations"]:checked').length+websites.children.length;
document.getElementById('selected-count').textContent=n+' selected';
}
form.querySelectorAll('input[name="destinations"]').forEach(el=>el.addEventListener('change',updateCount));
form.addEventListener('submit',e=>{
if(!form.querySelector('input[name="destinations"]:checked') && !websites.children.length){
e.preventDefault();alert('Choose where this post should go.');return;
}
if(when.value==='later'){
const d=new Date(date.value);
if(!date.value||isNaN(d.getTime())){e.preventDefault();alert('Choose a valid date.');return}
document.getElementById('utc_due').value=d.toISOString();
}
document.getElementById('time_zone').value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
});
</script></body></html>
"""

LOGIN = """<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto Poster AI</title>
<style>body{background:#101218;color:white;font:16px system-ui;max-width:370px;margin:15vh auto;padding:20px}
input,button{box-sizing:border-box;width:100%;padding:15px;margin:10px 0;border-radius:10px;font:inherit}
input{background:#222834;color:white;border:1px solid #444}
button{background:#beee83;border:0;color:#122007;font-weight:700}
p{color:#b1b9c9}</style><h1>✦ Auto Poster AI</h1><p>Sign in to your private starter dashboard.</p>
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
<p>Tell Auto Poster AI what source you want to monitor. This is for <b>new content</b>, not repeating the same message.</p>
<section>
<h2>New website source</h2>
<form method="post" action="/website">
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
<button type="submit">Save website request</button></form></section>
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
            c.execute("INSERT INTO website_requests(url,topic,cadence) VALUES(?,?,?)", (url, topic, cadence))
        return redirect("/website")
    with db() as c:
        rows = c.execute("SELECT * FROM website_requests ORDER BY id DESC LIMIT 50").fetchall()
    return render_template_string(WEBSITE_PAGE, requests=rows, csrf=session["csrf"])


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        abort(503, "Configure AUTOPOST_APP_PASSWORD on the server first.")
    if request.method == "POST":
        provided = request.form.get("password", "")
        if hmac.compare_digest(provided, PASSWORD):
            session.clear()
            session["authorized"] = True
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect("/")
        abort(401, "Wrong password")
    return LOGIN


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.before_request
def protect():
    if request.endpoint in ("login", "static"):
        return
    require_login()
    if request.method == "POST":
        # Browser form requests must include a session CSRF token.
        if not hmac.compare_digest(
            request.form.get("_csrf", ""), session.get("csrf", "missing")
        ):
            abort(403)


@app.route("/")
def home():
    with db() as c:
        rows = c.execute("SELECT * FROM app_posts ORDER BY id DESC LIMIT 100").fetchall()
        counts = {r["status"]: r["n"] for r in c.execute(
            "SELECT status,COUNT(*) n FROM app_posts GROUP BY status"
        )}
    return render_template_string(
        PAGE, posts=rows, counts=counts, csrf=session["csrf"],
        fb_enabled=bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")),
        other_apps=("instagram", "tiktok", "youtube", "linkedin", "pinterest", "threads", "x"),
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
    result = [(p, None) for p in platforms]
    result += [("website", url) for url in websites]
    if not result or len(result) > 12:
        raise ValueError("Add at least one app or website")
    return result


@app.route("/schedule", methods=["POST"])
def schedule():
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
    facebook_ready = bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN"))
    with db() as c:
        for platform, website_url in targets:
            supported = platform == "draft" or (platform == "facebook" and facebook_ready)
            status = "scheduled" if supported else "needs_connection"
            series_id = None
            if frequency != "once" and supported:
                cursor = c.execute(
                    "INSERT INTO app_series(content,platform,frequency,time_zone) VALUES(?,?,?,?)",
                    (content, platform, frequency, zone_name),
                )
                series_id = cursor.lastrowid
            c.execute(
                "INSERT INTO app_posts"
                "(content,platform,due_at,status,series_id,frequency,website_url)"
                " VALUES(?,?,?,?,?,?,?)",
                (content, platform, due, status, series_id, frequency, website_url),
            )
    return redirect("/")


@app.route("/cancel/<int:post_id>", methods=["POST"])
def cancel(post_id):
    with db() as c:
        post = c.execute("SELECT series_id FROM app_posts WHERE id=?", (post_id,)).fetchone()
        if post and post["series_id"]:
            c.execute("UPDATE app_series SET active=0 WHERE id=?", (post["series_id"],))
            c.execute("UPDATE app_posts SET status='cancelled' "
                      "WHERE series_id=? AND status='scheduled'", (post["series_id"],))
        else:
            c.execute("UPDATE app_posts SET status='cancelled' "
                      "WHERE id=? AND status='scheduled'", (post_id,))
    return redirect("/")


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
