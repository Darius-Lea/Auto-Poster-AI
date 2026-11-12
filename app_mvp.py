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
from datetime import datetime, timezone
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


def run_due(now=None):
    """A single worker pass. Delivery errors are held instead of blindly retried."""
    handled = 0
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
            else:
                # A successful draft is NOT a successful social media delivery.
                state = "draft_ready"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            state = "needs_review"
        with db() as c:
            # Recover interrupted uploads as needs_review rather than repeating them.
            c.execute(
                "UPDATE app_posts SET status=?,remote_id=?,error=? "
                "WHERE id=? AND status='sending'",
                (state, remote, error, job["id"]),
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
<title>Auto Poster AI · Starter</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
body{margin:0;background:#101218;color:#f1f3f7}
header{max-width:950px;margin:auto;padding:26px 20px;display:flex;justify-content:space-between;align-items:center}
main{max-width:950px;margin:auto;padding:0 20px 60px}
h1{font-size:2rem;letter-spacing:-.04em;margin:12px 0}h2{font-size:1.13rem}
p,small,.muted{color:#a8afbf;line-height:1.55}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
.panel{padding:22px;background:#1c202a;border:1px solid #303542;border-radius:18px;margin:14px 0}
input,textarea,select,button{font:inherit;box-sizing:border-box}
input,textarea,select{background:#11151e;color:white;width:100%;padding:13px;border:1px solid #444c5b;border-radius:10px;margin:7px 0 16px}
textarea{min-height:140px;resize:vertical}
button{padding:13px 20px;background:#beee83;color:#14210c;border:0;border-radius:10px;font-weight:700;cursor:pointer}
a{color:#beee83}.pill{display:inline-block;background:#303848;padding:5px 10px;border-radius:20px;font-size:.8rem}
.post{padding:14px 0;border-top:1px solid #363c4a;overflow-wrap:anywhere}
.error{color:#ffb3b3}label{font-weight:600;font-size:.9rem}
nav{display:flex;gap:16px}
@media(max-width:600px){header{align-items:flex-start;gap:12px;flex-direction:column}nav{flex-wrap:wrap}}
</style></head><body>
<header><strong>✦ Auto Poster AI</strong>
<nav><a href="/">Home</a><a href="/logout">Sign out</a></nav></header>
<main>
<span class="pill">Starter · Single owner</span>
<h1>Your content, on autopilot.</h1>
<p>Write once. Schedule it. Keep track. The Facebook Page connector uses the existing
account authorization stored on your server. Other destinations currently save drafts only.</p>
<div class="grid">
<div class="panel"><h2>Scheduled</h2><h1>{{ counts.get("scheduled",0) }}</h1></div>
<div class="panel"><h2>Published</h2><h1>{{ counts.get("published",0) }}</h1></div>
<div class="panel"><h2>Needs attention</h2><h1>{{ counts.get("needs_review",0) }}</h1></div>
</div>
<div class="grid">
<section class="panel">
<h2>Create a post</h2>
<form method="post" action="/schedule">
<label for="content">What's your post about?</label>
<textarea id="content" name="content" maxlength="10000" required placeholder="Write a post for any topic or audience."></textarea>
<label for="platform">Destination</label>
<select name="platform" id="platform">
<option value="draft">Save as a draft (no social publishing)</option>
<option value="facebook">Facebook Page (requires configured Page access)</option>
</select>
<label for="due">Post at (your local time)</label>
<input type="datetime-local" id="due" name="due" required>
<input type="hidden" id="utc_due" name="utc_due">
<button type="submit">Schedule post</button>
</form>
</section>
<section class="panel">
<h2>Autopilot health</h2>
<p><strong>Background scheduler:</strong> Running while this server process is running.</p>
<p><strong>Facebook:</strong> {{ "Server credentials configured" if fb_enabled else "Needs server-side Page authorization" }}</p>
<p><strong>Canva and other networks:</strong> Not connected to this starter app yet.</p>
<p><strong>Limits:</strong> Text posts only for this first MVP. Images, video, feeds, OAuth, billing, and analytics come next.</p>
<p><strong>Safety:</strong> Failed or uncertain uploads are held for review, not automatically resent.</p>
</section>
</div>
<section class="panel"><h2>Post history</h2>
{% for p in posts %}
<div class="post"><span class="pill">{{p.status}}</span>
<strong>{{p.platform}}</strong> · <small>{{p.due_at}}</small>
<p style="white-space:pre-wrap;color:#f0f2f7">{{p.content}}</p>
{% if p.remote_id %}<small>Post ID: {{p.remote_id}}</small>{% endif %}
{% if p.error %}<p class="error">{{p.error}}</p>{% endif %}
{% if p.status == 'scheduled' %}
<form method="post" action="/cancel/{{p.id}}"><button type="submit">Cancel scheduled post</button></form>
{% endif %}
</div>
{% else %}<p>No posts yet. Make your first one above.</p>{% endfor %}
</section>
</main>
<script>
const form=document.querySelector('form[action="/schedule"]');
form.addEventListener('submit', e=>{
const input=document.getElementById('due');
const d=new Date(input.value);
if(!input.value || isNaN(d.valueOf())) { e.preventDefault(); alert('Choose a valid time'); return; }
document.getElementById('utc_due').value=d.toISOString();
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
    page = PAGE.replace('<form method="post" action="/schedule">',
        '<form method="post" action="/schedule"><input type="hidden" name="_csrf" value="{{csrf}}">')
    page = page.replace('<form method="post" action="/cancel/{{p.id}}">',
        '<form method="post" action="/cancel/{{p.id}}"><input type="hidden" name="_csrf" value="{{csrf}}">')
    return render_template_string(page, posts=rows, counts=counts, csrf=session["csrf"],
                                  fb_enabled=bool(os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")))


@app.route("/schedule", methods=["POST"])
def schedule():
    content = request.form.get("content", "").strip()
    platform = request.form.get("platform")
    if not content or len(content) > 10000 or platform not in ("draft", "facebook"):
        abort(400, "Invalid post or destination")
    try:
        due = valid_due(request.form.get("utc_due"))
    except ValueError as exc:
        abort(400, str(exc))
    if platform == "facebook" and not (
        os.getenv("META_PAGE_ID") and os.getenv("META_PAGE_ACCESS_TOKEN")
    ):
        abort(400, "Configure the Facebook Page connection on the server first")
    with db() as c:
        c.execute("INSERT INTO app_posts(content,platform,due_at) VALUES(?,?,?)",
                  (content, platform, due))
    return redirect("/")


@app.route("/cancel/<int:post_id>", methods=["POST"])
def cancel(post_id):
    with db() as c:
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
