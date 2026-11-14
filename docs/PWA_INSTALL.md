# Installable web app

Open `/install` on the deployed HTTPS service. Supported Chrome/Edge browsers can
offer an install prompt; iPhone/iPad users use Safari → Share → Add to Home Screen.
This is a web app installation, not an Apple, Google or Microsoft store listing.

The manifest opens `/launch?source=installed` within the service's `/` scope. Its
origin comes from the deployed website, not a hard-coded Render hostname. Account
access, shared server drafts and approved schedules use the same backend as the
website. Website changes load over the network without a replacement installer.

The root service worker only caches the fixed public `/offline` document and four
brand assets. It never stores navigation responses, customer pages, API data,
uploads, captions, authorization callbacks or POST requests. Install/precache
fetches omit cookies and reject redirects. Unavailable network navigation shows
the public reconnect screen; private workspace editing is not an offline feature.

The cache is versioned and cleanup only removes this feature's prior public
caches. Signing out clears the existing account session; no account pages were
stored by this worker. Native client packaging has separate documentation.

Checks: `python -m unittest discover -s tests -p test_pwa.py -v` and
`python scripts/qa/install_clip_smoke.py` (temporary data, Chromium).
