# Connect FORT WAYNE GONE WILD to Facebook

Use Facebook's Page API for this first rollout. You do not need to give this
application your Facebook password or automate Facebook's website. The current
application is a scheduled Python pipeline, with no account-login screen yet.

## 1. Get access to your Page

In your Meta developer app, adding `pages_show_list`, `pages_read_engagement`, and
`pages_manage_posts` is the right starting point. Adding them in the dashboard does
not mean a token has been granted those permissions.

1. Open Meta's **Graph API Explorer**, select your own app, and select **User
   Token**.
2. Request `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`.
3. Generate the token, complete Facebook's consent screens, and include **FORT
   WAYNE GONE WILD** when choosing Pages.
4. In the Explorer's request field, run this **GET** request:

   ```text
   me/accounts?fields=id,name,access_token,tasks
   ```

5. Find the object whose `name` is **FORT WAYNE GONE WILD**. Its `id` is your Page
   ID, and its `access_token` is the **Page token**. Use this token, not the User
   Token from the Explorer's token box.

If the Page is missing, check the Facebook account used for consent, the Pages
selected during consent, and your Page access. Your account needs a task that
allows creating Page content. While the Meta app is in development, the person
authorizing it generally also needs an app role. Follow Meta's current dashboard
requirements for your app and business setup.

Treat tokens like passwords. Do not paste them into chat, issues, screenshots,
source files, or browser address bars. Do not put your App Secret in an address
bar to exchange tokens. Page tokens can expire or be revoked; they are not
permanent keys. For a longer-lived connection, use Meta's supported token tools
and current documentation, or a server-side OAuth flow. The connection will still
need reconnection handling.

## 2. Save the connection in GitHub

Open your repository's **Settings → Secrets and variables → Actions**.

Add these under **Secrets**:

| Secret | Value |
|---|---|
| `META_PAGE_ID` | The selected Page's `id` |
| `META_PAGE_ACCESS_TOKEN` | That same Page's `access_token` |

Add these under **Variables**:

| Variable | Initial value |
|---|---|
| `PUBLISH_BACKEND` | `meta` |
| `MAX_PER_RUN` | `1` |
| `META_GRAPH_VERSION` | `v26.0`, or a supported version selected for your app |
| `AUTOPOST_ENABLED` | `false` until your one-post test succeeds |
| `WANTED_ENABLED` | `false` until the wanted source is verified and its preview succeeds |

`v26.0` is the configurable starting value for the supplied plan, not a claim that
it is the latest version. Confirm a supported version in Meta's current tools.
Existing Gemini and Telegram settings can remain in place. Gemini is optional:
without its key the application uses factual template captions.

## 3. Check, preview, then publish one post

In GitHub, open **Actions → Auto Post → Run workflow**. Use the updated default
branch containing this code.

1. Select **check-facebook**. This performs a read-only identity check and should
   log the expected Page name and ID. It does not prove that publishing permission
   has been granted and does not upload anything.
2. Select **preview**, with **limit = 1**. It scrapes and prepares one candidate
   without publishing. If Telegram is configured, it sends a preview summary and
   image there. Check the image, caption, and source details.
3. Select **publish**, with **limit = 1**. This creates a real public Page post.
   Check the Page itself. A confirmed delivery is stored with Facebook's returned
   post/photo ID.
4. Once that post looks correct, set the GitHub Actions variable
   `AUTOPOST_ENABLED` to `true`. This enables scheduled Facebook posting.

Manual runs are available while `AUTOPOST_ENABLED` is false. Scheduled runs with
`PUBLISH_BACKEND=meta` stay paused until it is true. Existing queue/Telegram
schedules are not affected by this Facebook gate. Set it back to false to pause
scheduled Facebook publishing while keeping the connection saved.

If there are no eligible new records, a run can finish without a post. The Allen
County Sheriff roster is the active booking source. The supplied Allen County
TheMostWanted.net URL is configured for preview, with publication blocked until
its actual listings and page layout are verified.

The schedule runs three times daily around 9:17 a.m., 1:17 p.m., and 7:17 p.m.
Fort Wayne time, adjusting for daylight saving time. Scheduled runs attempt at
most one record each. GitHub's schedule can be delayed or skipped; a run can
also have no eligible candidate, so actual daily volume can be lower. Manual
publish runs add to this scheduled volume. These are starting times to test,
not proven optimal times for Facebook's algorithm. After two weeks, compare
reach and engagement in Meta Business Suite and adjust using your Page's results.

The Allen County adapter now includes every booking date in the current roster,
with the oldest unposted bookings first. The workflow sets `MAX_AGE_DAYS=0` so
an old three-day repository variable cannot exclude long-term inmates. The
roster is refreshed on each run: released people who no longer appear are not
kept in a stale publishing queue. Missing photos or failed source requests are
reported and can prevent a record from being published.

With roughly 780 records, three posts daily would require about 260 days before
accounting for new arrivals and releases. A small daily limit cannot also
guarantee immediate coverage of the full roster. The initial backfill pace still
needs to be selected by the owner; the schedule is a starting point, not an
analytics-driven optimizer.

## Daily roster status updates

Once the updated code is merged and `AUTOPOST_ENABLED=true`, a separate daily
check runs around 8:17 a.m. Fort Wayne time. It does not publish or reshare
anything: it checks the full current Allen County roster and edits existing
Facebook booking captions using their stored Facebook post/photo IDs.

The original booking post, photo, date, caption, and comments remain in place.
The app adds a dated note **(UPDATE: NO LONGER LISTED IN ALLEN COUNTY JAIL)**
after two successful daily scans, at least 20 hours apart, show that booking
missing. This means the public roster no longer lists the booking; it is not
confirmation of release, freedom, dismissal, or transfer to another facility.
The app does not automatically assert **RELEASED** from absence alone. If the
same booking reappears, it removes its own status note and retains the original
caption. Manual edits to the booking text are preserved when adding/removing
the managed note.

Empty scans, malformed person rows, incomplete pagination, a mismatch with the
site's declared roster count, or a roster count falling by more than 20% do not
trigger disappearance updates. Such runs fail visibly for investigation. A failed
Facebook edit remains pending for a later daily check. The app verifies that
the stored post/photo belongs to the configured Page before reading or editing
its caption; it never deletes or recreates the post.

Use **preview-status** first for a read-only preview. It copies the database to
memory so it does not advance the saved daily observations or edit Facebook.
**check-status** applies the daily check and any eligible caption updates. Limit
is ignored by status checks: all tracked bookings are checked, not just that
run's publishing limit. GitHub schedules may be delayed or skipped, so this is
not a real-time custody directory and a note can take more than two days.

New confirmed posts save the source identity, original caption, and returned
Facebook ID for tracking. Earlier confirmed delivery records can be attached
when their matching booking is still present on the roster. Older posts without
a matching receipt or source mapping cannot be inferred or automatically updated.
The original post already made during setup has no stored caption/source metadata
in the old schema; the first check attempts to recover it while its booking is
still listed. Manual Facebook posts are not automatically imported.

No live caption edit was verified during development: cloud credentials and
source access remain unavailable. Ownership checks and the Graph API's actual
response must succeed on the live Page. Cache eviction still loses monitoring
history, so durable storage remains necessary for dependable long-term operation.

## One wanted listing daily

Manual runs have a source selector: `booking`, `warrant`, or `all`. The wanted
source is `https://www.themostwanted.net/Indiana/Allen/Browse/New`, supplied by
the owner. Choose **preview** with source **warrant** and limit **1** to inspect
it without publishing. The current generic selectors are unverified against this
site. A blocked request, incompatible layout, or zero valid records fails clearly.

After live verification, source-specific parsing tests, and a correct preview,
the source's `verified` setting in `config/cities.json` can be set to `true`.
Only then set GitHub Actions variable `WANTED_ENABLED=true`. Scheduled wanted
runs also require `PUBLISH_BACKEND=meta` and `AUTOPOST_ENABLED=true`. The separate
slot attempts one wanted post around 4:17 p.m. Fort Wayne time, adjusting for DST.
It adds to the three booking slots. Wanted captions are labeled **MOST WANTED**,
link to their source, and do not describe the person as booked or convicted.

Wanted publishing is deliberately inactive while verification is incomplete.
The current cloud network policy denied the wanted-site request with HTTP 403;
the saved environment-settings draft adds `www.themostwanted.net` and
`themostwanted.net`. Review/save that network configuration to allow verification,
then publish the environment after setup validation. No live wanted-page scrape
or wanted Facebook post was verified in this development run.

## Delivery history and retries

Do **not** delete database caches to move from Telegram/queue to Facebook. This
version keeps delivery history separately for each destination, including the
Facebook Page and workspace. The old global `seen` table is preserved but is not
imported as Facebook deliveries. Current roster records can therefore be candidates
again after upgrading, even if they were previously queued or sent to Telegram.
If the old code also posted some of them to Facebook, inspect existing posts
before enabling the new destination.

A definite Facebook rejection is reported as a failed run, with Meta's useful
error details. The record remains eligible for a later attempt after the cause
is fixed. A timeout or interrupted upload can mean Facebook accepted the post
without returning a usable response. Such a delivery is held for reconciliation
instead of being posted again automatically.

From a checkout using the **same delivery database**, list held records:

```bash
python main.py --deliveries
```

Check the Facebook Page. If the post exists, record its confirmed ID:

```bash
python main.py --resolve-record HASH --destination meta:PAGEID:WORKSPACE --remote-ref meta:POSTID
```

Only after checking that no post was created, allow a later retry:

```bash
python main.py --resolve-record HASH --destination meta:PAGEID:WORKSPACE --retry-confirmed-absent
```

Replace the placeholders with the exact hash and destination printed by
`--deliveries`. For the configured city, `WORKSPACE` is `fort-wayne`. These
commands update the local database only; they do not inspect or delete Facebook
posts. If the run happened on GitHub, reconciliation must use its restored
database and persist the corrected database for later runs. Editing an unrelated
local database will not repair GitHub's history.

GitHub Actions caches are best-effort storage and can be evicted. This setup
reduces duplicates while history is retained; it cannot promise permanent
exactly-once publishing. Keep durable database storage as the next reliability
upgrade before offering paid accounts.
