# Test Auto Poster AI — working single-owner starter

This app is separate from the legacy scheduled Fort Wayne pipeline. The legacy
GitHub Actions workflow continues unchanged on `main`.

## Exactly what works in this first build

- Password-protected mobile-friendly web dashboard
- Compose arbitrary text (news, personal, business, pet adoption, creators, etc.)
- Tap **+ Add apps or websites** to choose multiple destinations in one post
- Select **Post now / Schedule later** and **Once / Daily / Weekly**
- Repeating supported posts reschedules the same text after a confirmed success; click **Stop repeating** to halt a series
- In the Post composer, **Post to my website** records a destination request,
  but it remains **Needs connection** until a site-specific publishing integration exists
- The separate **Watch a website** screen collects a monitoring request without
  publishing anything. It does not yet scrape or poll the URL; it is setup only
- Schedule text-only posts to an authorized Facebook **Page** using the
  existing official Meta Page API client
- Schedule posts to the **draft** destination: those are saved internally and
  marked draft_ready when due, **not** published to a social network
- Save intended Instagram, TikTok, YouTube, LinkedIn, Pinterest, Threads, X and
  arbitrary website destinations as **Needs connection**. These are NOT sent.
- See queued, published, needs-review and cancelled statuses
- Background server scheduler running every ~20 seconds while server is up
- Manual cancellation of scheduled posts
- Hold ambiguous deliveries for review instead of automatically reposting them

## Not implemented yet

No Canva connection in this build. No photo/video uploading, live website
scraping or polling, RSS import, account OAuth connection wizard, Facebook personal-profile
posting, Instagram/YouTube/TikTok/X/LinkedIn direct publishing, real analytics,
automatic caption generation, subscription billing, tenant accounts, or App Store
binary. Those features are planned work, not operational here.

**Do not give this starter to customers yet.** Its password is shared between
all visitors and it does not isolate tenants. Deploy privately for the owner only.

## Test locally

1. From this feature branch, install Python 3.11 and dependencies:
   `pip install -r requirements-app.txt`
2. Set `AUTOPOST_APP_PASSWORD` to a new long random password (12+ characters).
3. Set `AUTOPOST_WEB_SECRET` to a different strong random 32+ character value.
4. Optional: to publish to the authorized Facebook **Page**, set
   `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN`, and optionally
   `META_GRAPH_VERSION`. These secrets belong server-side only.
5. Run `python app_mvp.py`.
6. Open `http://127.0.0.1:8080`, sign in and schedule a **draft** post first.
7. For production-like local WSGI testing, set
   `AUTOPOST_COOKIE_SECURE=false` when using plain HTTP, then run
   `gunicorn --workers 1 --threads 2 --bind 127.0.0.1:8080 app_mvp_wsgi:app`.
   Set this back to true behind HTTPS.

Use the **Post** composer and open **+ Choose where to post**. Pick one or more
apps, add your own website publishing destination if desired, then Post now or choose
an exact local date/time. The server stores UTC for the due time and the local
timezone for repeating schedules. Daily and weekly repeats are hidden in Advanced and repeat the **same text**;
they create the next job only after the previous delivery is confirmed or a draft is ready; errors
and uncertain outcomes hold the series rather than silently reposting.
Leave the server running for unattended delivery.

**Important:** Facebook posting publishes to the configured Page when due.
Use a test Page, or verify the content and schedule carefully before testing
against a live business Page.

## Try on iPhone using Render

Use the `render-mvp.yaml` Blueprint on this branch to create a separate paid
persistent web service. Fill in secrets through Render's dashboard. After a
successful deployment visit the Render HTTPS URL on your phone.

The existing `render.yaml` and GitHub Actions configuration are left alone.
Do not copy your Facebook Page token into source code or user-visible forms.
Do not use Render's ephemeral filesystem for reliable scheduling.

## Operational caveats

- **One Gunicorn worker only**; multiple workers create duplicate scheduler
  threads. SQLite is single-instance and must live on persistent disk.
- A lost response after a possible Facebook publication becomes
  `needs_review`; manually inspect the Page before deciding to repost.
- Stopping the app stops the scheduler; an always-on server is required.
- Do not put unauthenticated endpoints or real customer passwords into this app.
- Hosted delivery is not verified until you actually deploy the service and
  confirm a Facebook Page post ID.
- The developer still needs Meta credentials and required approvals. Customers
  will use OAuth in the future customer version, not developer keys.

## Website source versus destination

- **Post to my website** means delivering content **to** a connected CMS/blog.
  This starter only records the request; it cannot post to arbitrary URLs.
- **Watch a website** means monitoring a permitted source for new items.
  It has its own `/website` setup screen and separate table; **no polling,
  scraping or auto-publishing happens yet**. A URL alone does not authorize
  scraping, copying media, or posting.
- A brand-new text post defaults to **Once**. The advanced Daily/Weekly option
  explicitly repeats identical content; it does not synthesize fresh posts.
