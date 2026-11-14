"""Production WSGI entry point for Auto Poster AI.

Run one Gunicorn worker to avoid duplicate scheduler loops. This prototype's
SQLite database and Clip Studio media require persistent storage. This entry
point starts the publishing and clip schedulers; never use multiple workers.
"""
import os
import secrets
import threading

from app_mvp import app, init_db, recover_interrupted, worker

password = os.getenv("AUTOPOST_APP_PASSWORD", "")
web_secret = os.getenv("AUTOPOST_WEB_SECRET", "")
if len(password) < 12:
    raise RuntimeError("Set AUTOPOST_APP_PASSWORD with at least 12 characters")
if len(web_secret) < 32:
    raise RuntimeError("Set AUTOPOST_WEB_SECRET with at least 32 unpredictable characters")

app.secret_key = web_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("AUTOPOST_COOKIE_SECURE", "true").lower() == "true",
    MAX_CONTENT_LENGTH=64 * 1024,
)
init_db()
recover_interrupted()
threading.Thread(target=worker, daemon=True, name="autopost-web-scheduler").start()
