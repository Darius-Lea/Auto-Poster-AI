# Account access repair — November 17, 2026

## Reproduced failure

The direct WSGI entry point, `app_mvp:app`, could serve registration and sign-in
forms before initializing the complete database. With fresh storage, account
POSTs failed because `customer_auth_limits` was absent. With an account-only
database, registration or login succeeded but the redirected workspace failed
because `app_posts` was absent. Real local Gunicorn requests reproduced this
second failure. The initialized `app_mvp_wsgi:app` entry point succeeded.

This matches the reported symptoms but does not establish the live Render
service's entry point or error log.

## Repair

- Initialize and migrate the full schema once per process/database before
  account writes or authenticated workspace requests. Serialize the first
  initialization to avoid concurrent migration races. Anonymous account forms
  remain independent of database, model and provider calls.
- Commit account creation and default workspace controls together. Avoid a
  fallible initialization/read after successfully committing the account.
- Bound account database waits to two seconds and return an actionable 503
  retry form for transient storage failures. Preserve the session and CSRF
  context; omit password values and internal database details from the page.
- Show immediate registration/sign-in progress, prevent duplicate submissions,
  explain a slow response, and restore controls after back navigation.
- Add a mobile/desktop Tools sidebar for navigation; its browser verification
  is recorded separately by the release task.

## Verified account workflow

`scripts/qa/account_access_smoke.py` passed on phone and desktop using temporary
fresh and legacy databases, with no explicit `init_db()` call in its server.
It exercised registration, logout, returning sign-in, private tools, pending
feedback, an actual SQLite write lock producing a bounded 503, and a successful
retry. No duplicate account, JavaScript error or horizontal overflow was found.
No external services were used.

Observed local browser durations were 0.438 seconds for phone registration,
0.283 seconds for phone sign-in after retry, 0.295 seconds for desktop
registration, and 0.216 seconds for desktop sign-in after retry. These are local
fixture observations, not production latency promises. Final full-suite and
sidebar check results must be recorded by the release task before pushing.

## Deployment limits

The development environment still has no applied custom network grant for
`auto-poster-ai-free-preview.onrender.com` and no Render deployment connector.
Earlier live checks were denied before reaching the site. A code push does not
verify a new Render deployment, actual customer email delivery, live account
persistence or production latency. Store publication also still requires
signed packages, developer accounts and each store's review; it cannot be
established by this repair or a two-minute deadline.

Final validation: **511 Python tests passed**. The account-access browser check passed on phone and desktop with fresh/legacy storage, pending feedback, a real write-lock 503 and successful retry. The sidebar browser check passed open/close, Escape/focus, backdrop, sign-in and registration navigation. All are local fixtures; live Render deployment and store publication remain unverified.
