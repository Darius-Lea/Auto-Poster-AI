# Publish the current roster in groups of ten

After this update is merged into `main`:

1. Open **Actions → Booking Catch-Up → Run workflow**.
2. Leave the branch as **main** and click the green **Run workflow** button once.
3. Open the run to see its progress. Each active batch publishes one Facebook
   post with up to ten separate booking photos and a numbered caption.
4. For the regular schedule afterward, make sure **Settings → Secrets and
   variables → Actions → Variables → AUTOPOST_ENABLED** has the value `true`.
   This is a variable, not a secret. Keep `PUBLISH_BACKEND` set to `meta`.

Your existing `META_PAGE_ID` and `META_PAGE_ACCESS_TOKEN` secrets are used.
No new Facebook login, Telegram setup, or AI key is needed for this catch-up.
**Keep the database caches and the new progress history.** Deleting Telegram
messages does not affect Facebook posting or its delivery history.

## What this run posts

The run scans the complete current Allen County booking roster, including older
booking dates. It downloads fresh booking details and photos, applies the brand
logo, and uses factual template captions with source links and the presumption
of innocence. Existing output images and Telegram test messages are not used.

Prior individually posted current bookings are included, so the person in the
first test post is included too. Their old local receipts are archived, and
their new grouped photos become the tracked copies. The old Facebook test
post is not deleted automatically; you can delete it yourself.

Ten consecutive eligible records form a post; the final post may have fewer.
Source pages can change while the run is active, so a group is not tied to a
permanent source-page number. Each batch rescans the current roster and skips
confirmed group members. Someone already removed from the source is not newly
published. About 799 current eligible records would need about 80 posts.

The workflow processes one group at a time, up to 100 groups per run. It can
take hours to scan the roster, fetch the photos, and finish all groups. GitHub
and Meta limits can delay or stop the run; no Facebook reach or optimal posting
time is guaranteed. Do not launch several catch-up runs at once.

Normal individual posting shares the same workflow lock and is also blocked by
the saved catch-up state while catch-up is active or paused. When catch-up
completes, normal runs skip everyone already covered by a grouped post and
publish new bookings individually on the existing schedule. Scheduled runs
require `AUTOPOST_ENABLED=true`. The catch-up does not enable wanted posting.

Daily status checks retain each original photo and grouped post, updating
both with the existing **NO LONGER LISTED IN ALLEN COUNTY JAIL** note after two
complete daily observations. A return to the roster removes the managed note.
Status checks can still be run manually while catch-up is paused. Scheduled
runs share the lock and wait until the catch-up workflow ends.

## If the run stops

Read the failed step before rerunning. Confirmed groups are retained in the
delivery database and are skipped when continuing. A failed roster scan or
photo download does not claim a successful Facebook post.

An uncertain Facebook response, an interrupted acknowledgement, or a recorded
failed group requires checking the Page and reconciling that group first.
Do not clear caches or repeatedly rerun to force it through. Known Facebook
photo IDs and the upload stage are saved for that review; this workflow does
not automatically recreate a group with an unresolved outcome.

Every active batch saves its SQLite history and verifies that its exact cache
checkpoint exists before another batch proceeds. Each batch also uploads a
`catchup-history-...` artifact containing a recovery database for 30 days. If
the checkpoint or recovery upload fails, stop and recover the latest history
before rerunning. Do not resume using an older database after newer Facebook
posts have already been accepted.

If 100 groups were insufficient, the final verification fails rather than
claiming completion. With valid saved history and no unresolved group, a new
run continues with current bookings still uncovered. GitHub caches can be
evicted; they are not a substitute for a durable database in a subscription
service.
