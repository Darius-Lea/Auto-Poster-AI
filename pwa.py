"""Public install surface and a public-only offline shell; no account content is cached."""
from pathlib import Path
from flask import Blueprint, Response, render_template

pwa_bp = Blueprint("pwa", __name__)
ROOT = Path(__file__).resolve().parent


@pwa_bp.route("/install")
def install_page():
    response = Response(render_template("install.html"), mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    return response


@pwa_bp.route("/service-worker.js")
def service_worker():
    response = Response((ROOT / "static/service-worker.js").read_text(), mimetype="text/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@pwa_bp.route("/offline")
def offline_page():
    # This document is deliberately constant: never render user names, drafts or sessions.
    return Response('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b1424"><title>Connect to continue · Auto Poster AI</title><style>body{margin:0;background:#0b1424;color:#f3f7fc;font:17px/1.6 system-ui}main{max-width:560px;margin:12vh auto;padding:28px}img{width:64px;height:64px}h1{line-height:1.2}p{color:#bacbda}a,button{font:inherit;display:inline-block;border:0;border-radius:12px;background:#c8f68a;color:#16321c;padding:13px 18px;text-decoration:none}</style></head><body><main><img src="/static/brand/mark.svg" alt="Auto Poster AI"><h1>Connect to continue.</h1><p>Your app needs an Internet connection to load your workspace, sync drafts and manage cloud publishing. Private account pages and captions are not saved in this offline screen.</p><p>Approved server schedules can continue only while the hosted service and its connections remain available. This screen does not confirm whether a post was delivered.</p><a href="/launch">Try again</a></main></body></html>''', mimetype="text/html", headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})
