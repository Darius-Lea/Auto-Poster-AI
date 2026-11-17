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
    return Response('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b1424"><title>Connect to continue · Auto Poster AI</title><style>body{margin:0;background:#0b1424;color:#f3f7fc;font:17px/1.6 system-ui}main{max-width:560px;margin:12vh auto;padding:28px}img{width:64px;height:64px}h1{line-height:1.2}p{color:#bacbda}a,button{font:inherit;display:inline-block;border:0;border-radius:12px;background:#c8f68a;color:#16321c;padding:13px 18px;text-decoration:none;cursor:pointer}a{background:none;color:#c8f68a;padding-left:0}button:focus-visible,a:focus-visible{outline:3px solid #89d1ff;outline-offset:4px}</style></head><body><main><img src="/static/brand/mark.svg" alt="Auto Poster AI"><h1>Connect to continue.</h1><p role="status">We could not load this page. Your connection may be unavailable, or the service may be starting or taking longer than usual.</p><p>Choose Try again to reload the page you were opening. Your private account pages and captions are not stored in this connection screen.</p><button id="retry-connection" type="button">Try server again</button><script>document.getElementById("retry-connection").addEventListener("click",()=>{const target=new URL(location.href);if(target.pathname==="/offline")target.pathname="/welcome";target.searchParams.set("_ap_retry","1");location.replace(target.href)});if("serviceWorker" in navigator)navigator.serviceWorker.getRegistration().then(registration=>registration&&registration.update()).catch(()=>{});</script><p><a href="/account?mode=login">Go to sign in</a></p><p>Approved schedules depend on the hosted service and its connections remaining available. Check delivery confirmation when you reconnect; this screen cannot confirm whether a post was delivered.</p></main></body></html>''', mimetype="text/html", headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})
