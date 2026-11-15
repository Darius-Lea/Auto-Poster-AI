# Deploy the shared customer app and Clip Studio

Auto Poster and Clip Studio use **one customer account and one private workspace**.
Customers register their own email/password in `/account?mode=register`; they do
not use the operator's legacy owner password or supply developer keys. Password
hashes, profiles, connections and jobs are stored in the server database, never
GitHub. Logos, source videos, output clips and model files live in private data
storage. Do not commit databases, `.env` files or customer media.

## Select the correct service

The original `render.yaml` and `Dockerfile` run the owner's existing publisher.
They remain unchanged. `render-app.yaml` is an **opt-in, separate customer web
service** using `Dockerfile.app` and branch
`feature/saas-appstore-foundation-2026-11`.

The supplied current website is
`https://auto-poster-ai-free-preview.onrender.com`. A new Blueprint does not
replace that service or preserve its address automatically. To update that
existing service, inspect its actual branch, Docker path, variables and storage
in Render; back up and migrate any current database/media before changing them.
This workspace has not inspected or changed the Render account. The older
`render-mvp.yaml` installs only the text app dependencies and is insufficient
by itself for the video renderer.

Attach a durable disk at `/var/lib/autoposter`. The opt-in Blueprint requests a
paid `standard` instance and a 20 GB disk as a starting configuration, **not a
capacity guarantee**. Measure CPU/RAM/disk usage on representative source videos
before public distribution. Keep exactly one Gunicorn worker and one instance;
scaling needs a shared database, object store and external job workers first.
Free ephemeral/sleeping hosting cannot guarantee persistent accounts or ongoing
scheduled work. Keep backups, monitor free disk, and verify restart recovery.

The retained paths are:

| Data | Setting/path |
| --- | --- |
| Accounts, jobs and encrypted connection records | `AUTOPOST_WEB_DB=/var/lib/autoposter/app.db` |
| Private logos, uploads and rendered media | `AUTOPOST_CLIP_DATA_DIR=/var/lib/autoposter/clip-data` |
| Verified local speech weights/cache | `AUTOPOST_CLIP_MODEL_CACHE=/var/lib/autoposter/models/clip-studio` |
| Model download cache | `HF_HOME=/var/lib/autoposter/models` |

Set a stable random `AUTOPOST_WEB_SECRET` of at least 32 characters and keep
`AUTOPOST_COOKIE_SECURE=true`. Do not rotate encryption secrets without a
connection-token migration. `AUTOPOST_APP_PASSWORD` is optional and only enables
the separate legacy owner sign-in; it is not a customer password.

## Prepare speech recognition once on the host

The image includes FFmpeg/FFprobe, video dependencies, the bundled local sample,
and operator diagnostics. Rendering does not download a model during a customer
job. It requires local weights that have actually passed sample inference on
that host with the installed runtime versions.

In a private Render operator shell, run:

```bash
python scripts/qa/clip_transcription_smoke.py --warm --timeout 900
python scripts/qa/clip_transcription_smoke.py --verify --render \
  --output-dir /var/lib/autoposter/operator-check --timeout 900
```

`--warm` explicitly downloads the official configured model and performs real
sample transcription. The second command verifies existing local weights and
creates a clip with fresh transcription. Neither command posts a video. Review
the result and retain its safe diagnostic output. The default model is
`AUTOPOST_CLIP_MODEL=base.en`; an operator can provision verified local weights
with `AUTOPOST_CLIP_MODEL_DIR` instead. Keep required official model-provider
HTTPS egress available; do not disable TLS verification.

Optional `AUTOPOST_CLIP_WARM_ON_START=true` runs the bounded warmup before
Gunicorn starts and fails startup if verification fails. Leave it false when
using a prepared persistent cache or when platform startup deadlines make a long
initial download unsuitable. Runtime/dependency or weight changes invalidate
the stored health record and require verification again.

After actual storage, inference and full-render checks pass, set
`AUTOPOST_CLIP_ENABLED=true`. Before that, the app can accept accounts and free
internal drafts while clearly disabling unavailable rendering. The initial
preview limits are three renders per workspace per UTC day, two pending clips,
200 MiB uploads and one active renderer. Invalid pre-queue inputs refund their
attempt; real rendering attempts and revisions count toward the allowance.

## Connect the service, once, as operator

Configure the actual HTTPS `AUTOPOST_PUBLIC_URL`, working verification/recovery
SMTP, support address and legal service identity. New customer accounts can save
internal work immediately; email confirmation is required before external
connections, rendering and publishing. Confirm actual delivered email from a
fresh account rather than checking only that settings exist.

Configure the service-owned Meta app, approved permissions, exact callback and
encryption secret described in `CUSTOMER_BACKEND_2026-11-13.md`. Each customer
then signs in through Facebook and selects their own business Page. Facebook
video delivery uses scoped tokens and explicit destination/caption/time review.
An upload ID means processing; the worker marks publication only after the
provider confirms it. Test on an authorized test Page before advertising it.
Other social publishers and Reel-specific creation are separate capabilities.

`YOUTUBE_DATA_API_KEY` is an optional operator-only key for popular-video chart
inspiration. It is never requested from customers. Those dated observations are
not rankings of editing effects, trending audio or proof an edit caused success.
Curated caption looks are labelled separately. See `EDITING_TRENDS.md`.

Pasted-link import availability and supported sources are documented in
`VIDEO_LINK_IMPORT.md`. Imports validate public destinations and downloaded
containers; a watch-page URL is not universally a downloadable video. Networks
requiring an incompatible proxy disable pinned imports rather than bypassing
that network. Verify an actual supported import on the selected host.

Keep `AUTOPOST_PUBLIC_SALES_ENABLED=false` and release/legal/storage attestations
false until their actual requirements are met. Configure and test subscriptions
and signed webhooks separately; installing the image does not open paid sales.

## Verify the deployed revision and customer experience

```bash
python scripts/qa/render_release_check.py \
  --origin https://auto-poster-ai-free-preview.onrender.com \
  --expected-commit FULL_40_CHARACTER_RELEASE_COMMIT
```

Use the actual service origin if a new service receives a different address.
`/version` reports `RENDER_GIT_COMMIT` or `AUTOPOST_RELEASE_COMMIT`; unknown values
remain unknown. The public checker does not sign in, create content, email,
charge or publish. It cannot certify the private workflows.

Before inviting paying beginners, complete this short acceptance flow on the
real deployment: fresh signup → delivered email confirmation → saved personal
logo → permitted upload/import → fresh transcription/render → caption correction
→ reviewed test-Page schedule → provider-confirmed publication. A second account
must not see or download that customer's data. Log out/sign in again, recover a
password, restart the service, and verify saved accounts/branding/jobs remain.

This task's Render and official model requests were blocked by a development
network CONNECT 403. No live deployment, fresh inference, Facebook mutation or
store submission is established by local tests. Native client packaging needs
this same service's real HTTPS origin and separate signing/device/store checks.
