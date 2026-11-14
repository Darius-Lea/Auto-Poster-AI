"""Real-browser install/privacy and hosted Clip Studio checks using temporary data.

No render worker, provider request, paid model request, or publication is started.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_CACHE_PATHS = {
    "/offline",
    "/static/brand/mark.svg",
    "/static/brand/icon-192.png",
    "/static/brand/icon-512.png",
    "/static/brand/apple-touch-icon.png",
}


def cached_paths(page):
    return set(page.evaluate("""async () => {
      const names = await caches.keys();
      const requests = (await Promise.all(names.map(async name =>
        (await caches.open(name)).keys()))).flat();
      return requests.map(request => new URL(request.url).pathname);
    }"""))


def no_overflow(page, label):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), label


def run():
    with tempfile.TemporaryDirectory(prefix="autopost-install-clips-") as folder:
        env = {
            **os.environ,
            "AUTOPOST_WEB_DB": str(Path(folder) / "app.db"),
            "AUTOPOST_APP_PASSWORD": "synthetic-owner-password123",
            "AUTOPOST_WEB_SECRET": "synthetic-test-secret-longer-than-thirty-two",
            "AUTOPOST_REQUIRE_PAYMENT": "true",
            "AUTOPOST_PREVIEW_MODE": "true",
            "AUTOPOST_PUBLIC_LANDING": "true",
            "AUTOPOST_CLIP_ENABLED": "false",
            "AUTOPOST_CLIP_DATA_DIR": str(Path(folder) / "clips"),
        }
        code = (
            "import app_mvp;from werkzeug.serving import make_server;"
            "app_mvp.init_db();"
            "s=make_server('127.0.0.1',0,app_mvp.app,threaded=True);"
            "print(s.server_port,flush=True);s.serve_forever()"
        )
        log_path = Path(folder) / "server.log"
        with log_path.open("w") as log:
            server = subprocess.Popen(
                [sys.executable, "-c", code], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=log, text=True,
            )
            try:
                port = server.stdout.readline().strip()
                assert port.isdigit(), f"Test app did not start: {log_path.read_text()}"
                origin = "http://127.0.0.1:" + port
                artifacts = Path(os.getenv(
                    "AUTOPOST_QA_ARTIFACT_DIR",
                    str(Path(tempfile.gettempdir()) / "autopost-install-clips-qa"),
                ))
                artifacts.mkdir(parents=True, exist_ok=True)
                errors = []
                with sync_playwright() as playwright:
                    executable = os.getenv("HQ_CHROME_PATH") or shutil.which("chromium")
                    browser = playwright.chromium.launch(
                        headless=True, args=["--no-sandbox"],
                        **({"executable_path": executable} if executable else {}),
                    )
                    offline_cases = []
                    for name, viewport in [
                        ("phone", {"width": 390, "height": 844}),
                        ("desktop", {"width": 1440, "height": 1000}),
                    ]:
                        context = browser.new_context(viewport=viewport)
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        response = page.goto(origin + "/install")
                        assert response.status == 200
                        page.get_by_role("heading", name="Add Auto Poster AI to your device.").wait_for()
                        page.get_by_role("heading", name="iPhone and iPad").wait_for()
                        no_overflow(page, name + " install")
                        page.wait_for_function("navigator.serviceWorker.controller !== null")
                        scope = page.evaluate("async () => (await navigator.serviceWorker.ready).scope")
                        assert scope == origin + "/", scope
                        assert cached_paths(page) == PUBLIC_CACHE_PATHS
                        page.screenshot(path=str(artifacts / f"autoposter-install-{name}.png"), full_page=True)

                        # A genuine signed owner session visits private documents and API
                        # requests. Neither those responses nor logout enter Cache Storage.
                        page.goto(origin + "/login")
                        page.get_by_placeholder("App password", exact=True).fill(env["AUTOPOST_APP_PASSWORD"])
                        page.get_by_role("button", name="Sign in", exact=True).click()
                        page.wait_for_url(origin + "/")
                        for path in ["/studio", "/connections", "/control", "/clips"]:
                            response = page.goto(origin + path)
                            assert response.status == 200, (path, response.status)
                            no_overflow(page, name + " " + path)
                            if path == "/studio":
                                page.get_by_label("Your editable caption", exact=True).fill("PRIVATE_QA_CAPTION_" + name)
                                page.locator("#draft-save-status").filter(has_text="Draft saved to your workspace").wait_for()
                        assert "clip" in page.title().lower(), page.title()
                        # Processing is explicitly off, so this is a disabled hosted
                        # interface check rather than a media-rendering success claim.
                        assert "Clip rendering is not enabled" in page.locator("body").inner_text()
                        assert page.locator("#clip-submit").is_disabled()
                        assert page.locator('#clip-upload input[name="video"]').is_disabled()
                        page.screenshot(path=str(artifacts / f"autoposter-clips-{name}.png"), full_page=True)
                        jobs = page.evaluate("""async () => {
                          const response = await fetch('/clips/jobs', {headers:{Accept:'application/json'}});
                          return {status:response.status, data:await response.json()};
                        }""")
                        assert jobs == {"status": 200, "data": {"jobs": []}}, jobs
                        assert cached_paths(page) == PUBLIC_CACHE_PATHS
                        page.goto(origin + "/logout")
                        page.goto(origin + "/clips")
                        assert "/account?" in page.url and "next=clips" in page.url, page.url
                        denied = context.request.post(origin + "/clips/jobs", headers={"Accept": "application/json"})
                        assert denied.status == 401, denied.status
                        denied = context.request.get(origin + "/clips/jobs", headers={"Accept": "application/json"})
                        assert denied.status == 401, denied.status
                        assert cached_paths(page) == PUBLIC_CACHE_PATHS
                        cached_text = page.evaluate("""async () => {
                          const chunks = [];
                          for (const name of await caches.keys()) {
                            const cache = await caches.open(name);
                            for (const request of await cache.keys()) {
                              chunks.push(await (await cache.match(request)).text());
                            }
                          }
                          return chunks.join('\\n');
                        }""")
                        assert "PRIVATE_QA_CAPTION_" not in cached_text
                        offline_cases.append((name, context, page))

                    # Stop the fixture service as well as emulating offline. Chromium
                    # can keep service-worker fetches online under page emulation;
                    # stopping it guarantees a real network failure for the worker.
                    server.terminate()
                    server.wait(timeout=10)
                    for name, context, page in offline_cases:
                        context.set_offline(True)
                        response = page.goto(origin + "/launch?qa-offline=" + name, wait_until="domcontentloaded")
                        assert response.status == 200
                        assert page.get_by_role("heading", name="Connect to continue.").count() == 1, {
                            "url": page.url, "title": page.title(), "from_service_worker": response.from_service_worker,
                            "online": page.evaluate("navigator.onLine"), "headings": page.locator("h1").all_text_contents(),
                        }
                        assert "private account pages and captions are not saved" in page.locator("body").inner_text().lower()
                        no_overflow(page, name + " offline")
                        assert cached_paths(page) == PUBLIC_CACHE_PATHS
                        context.close()
                    browser.close()
                assert not errors, errors
                print(
                    "PASS: install instructions and root service worker on desktop/phone; "
                    "only five public resources cached across private pages, API requests and logout; "
                    "offline reconnect screen; signed Clip Studio and anonymous job rejection; "
                    "no JavaScript errors or horizontal overflow. No render or provider request started."
                )
            finally:
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=10)


if __name__ == "__main__":
    run()
