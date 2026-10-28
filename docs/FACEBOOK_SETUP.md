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

If there are no eligible new records, a run can finish without a post. Currently
only the Allen County Sheriff roster for Fort Wayne is configured; the
most-wanted adapter is present but no wanted listing is enabled.

The schedule runs four times daily at 00:17, 06:17, 12:17, and 18:17 UTC. GitHub's
schedule can be delayed. Keep the initial limit at one and review results before
increasing it; this is a per-run attempt limit, not a promise about daily volume.

## Delivery history and retries

Do **not** delete database caches to move from Telegram/queue to Facebook. This
version keeps delivery history separately for each destination, including the
Facebook Page and workspace. The old global `seen` table is preserved but is not
imported as Facebook deliveries. Recent records can therefore be candidates
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
