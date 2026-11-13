# Customer workbench update — November 13, 2026

The current branch now includes customer registration, isolated workspaces, the fixed fourth-step handoff, explicit Page-specific setup preparation, Basic daily/Page enforcement, creative automation, managed AI fallback, measured reporting and Canva connection code. See [CUSTOMER_BACKEND_2026-11-13.md](CUSTOMER_BACKEND_2026-11-13.md) for exact supported behavior and live-configuration limits. Earlier single-owner notes below describe the earlier release and are historical where superseded.

# Test Auto Poster AI — single-owner web beta

The current web app is on `feature/saas-appstore-foundation-2026-11`. It is separate from the legacy scheduled Fort Wayne publisher on `main`; changing the web app does not establish a change to that publisher or its deployment.

## Implemented behavior

- Public `/welcome` website and `/start` four-step planning flow: choose platforms, write a text post, choose timing and review. The anonymous setup is saved in browser storage with an optional downloadable backup.
- Private owner login and optional authenticated import of a public setup into an inactive plan. Importing a plan or paying does not turn on publishing.
- Mobile-friendly owner dashboard, saved automation preferences, text composition, multiple selected destinations, post review, one-time scheduling and optional daily/weekly repetition of the same text.
- Internal draft scheduling: due drafts become `draft_ready`; they are not sent to a social network.
- Facebook Page OAuth connection/discovery/selection/disconnection routes, encrypted token storage primitives and official Page API text publishing. These require configured service-owned Meta credentials, the appropriate permissions and live validation. They do not provide public customer accounts by themselves.
- Stripe checkout/portal routes, signature-verified subscription webhook handling and server-side entitlements. These require configured Stripe settings and live validation. Saving a plan never triggers a checkout or automatic activation.
- Saved `setup_only` posts when unpaid or in preview, with explicit later activation of supported publishing. An expired proposed posting time must be replaced before activation.
- A server scheduler while the service is running, cancellation/stop-repeat controls and holds for uncertain delivery outcomes instead of blind reposting.
- Original Signal Fold branding, responsive artwork, iPhone touch icon and PNG/SVG/maskable browser install icons.

## What remains unfinished or unverified

Public customer registration, independent accounts and tenant isolation are not implemented. The starter uses one owner password/workspace. Do not share that private owner account with unrelated customers.

Photo/video uploads, video clipping, Canva integration, live general website/RSS collection, most social publishing connectors and real account analytics remain unfinished. Selecting a planned network or source only saves an intention; it does not grant permissions or enable an integration. The legacy pipeline's caption/source features do not automatically make those features available in this general composer.

Facebook OAuth and Stripe code now exist, but production approvals, configured credentials, actual authorization/purchase flows and confirmed live delivery must still be verified. The previous statement that all OAuth/subscription code was absent is superseded by these implementation details. No native iOS/Android binaries or store release exist yet.

## Safe local walkthrough

1. Check out the web feature branch. Use a project-local Python environment and install `requirements-preview.txt` for the lightweight visual preview, or `requirements-app.txt` for the full app/regression dependencies.
2. Use a separate test database path in `AUTOPOST_WEB_DB`. Set `AUTOPOST_APP_PASSWORD` to a new long password (at least 12 characters) and `AUTOPOST_WEB_SECRET` to a stable unpredictable secret (at least 32 characters). Keep secrets out of source/chat.
3. Set `AUTOPOST_PREVIEW_MODE=true`, `AUTOPOST_PUBLIC_LANDING=true` and `AUTOPOST_REQUIRE_PAYMENT=true`. For local HTTP WSGI testing only, set `AUTOPOST_COOKIE_SECURE=false`; restore `true` behind HTTPS.
4. Run `python app_mvp.py`, then open `http://127.0.0.1:8080/welcome`. Alternatively run `gunicorn --workers 1 --threads 2 --bind 127.0.0.1:8080 app_mvp_wsgi:app`.
5. Complete `/start`, review and save. Reload and confirm the draft remains in that browser. Download a backup if desired. Sign in at `/login` and deliberately import the prepared setup; it remains inactive.
6. In the owner composer, choose the internal draft destination and review a post. Confirm its scheduled/status transitions, and test cancellation or repetition only with a draft. Preview mode blocks external publication, checkout and Facebook connection.
7. Verify `/icon.svg`, `/manifest.webmanifest` and the brand images load. Phone-sized browser checks do not establish physical iPhone installation behavior.

The scheduler runs while the process is running. One Gunicorn worker is required; multiple worker processes start duplicate scheduler loops. Daily/weekly repeats schedule the same text after confirmed success, preserve the chosen local timezone and hold on errors/uncertainty. They do not generate fresh content.

## Live owner testing

Use the existing web service and instructions in [RELEASE_2026-11-12.md](RELEASE_2026-11-12.md). No actual Render hostname or deployed branch is established by this repository. A GitHub push alone does not prove deployment.

Keep a durable SQLite path and use one service instance. Do not reset data to change branding or switch backends. A sleeping/ephemeral preview cannot provide reliable unattended delivery.

Only after explicitly preparing a controlled live test should preview mode be disabled and the required Meta/Stripe configuration enabled. Confirm the intended Facebook Page and permitted content, active entitlement, explicit post activation and returned remote post ID. Live platform actions and purchases are not covered by offline mock tests.

## Website source versus destination

- A website destination means delivering to a connected CMS/blog. Arbitrary website URLs in this starter remain setup intentions until a site-specific publisher exists.
- A website source means monitoring permitted material for new content. The `/website` screen records a request; it does not implement general polling, scraping or automatic publishing.
- The public wizard's date/frequency fields describe preferences until an account/integration and explicit activation complete the real workflow.

## Recorded validation

For the November 12, 2026 release candidate, the full offline suite passed **239 tests**. The focused public-onboarding, preview, unpaid-setup and web-starter suites passed **31 tests**, and `scripts/qa/brand_setup_smoke.py` passed real desktop/mobile Chromium walkthroughs for branded screens, setup, local save/restoration, dashboard and automation pages. These results do not certify production hosting, real external publication, physical mobile devices or store readiness. See [STORE_LAUNCH.md](STORE_LAUNCH.md) for the later Apple/Google launch stages.
