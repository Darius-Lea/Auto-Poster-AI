# Customer app and Clip Studio hosting

The existing `render.yaml` and `Dockerfile` run the original owner's publisher.
They are intentionally unchanged. Deploy the customer website from
`feature/saas-appstore-foundation-2026-11` with `Dockerfile.app` as a **separate
Render web service**. No deployment or live Render configuration was performed
from this workspace.

Attach persistent storage at `/var/lib/autoposter`. SQLite, uploads, renders and
speech-model cache live there. Back it up and monitor free space. Run exactly one
Gunicorn worker and one service instance; horizontal scaling requires a shared
database, object store and external render queue first. Reserve CPU/RAM for video
rendering; a sleeping/free service is unsuitable for reliable background work.

Provide stable `AUTOPOST_WEB_SECRET` (at least 32 random characters), strong
`AUTOPOST_APP_PASSWORD` (owner only), the actual HTTPS `AUTOPOST_PUBLIC_URL`, and
the current customer account/mail/Meta/Canva/billing settings described in
`ACCOUNT_RECOVERY.md`, `EDITOR_CONNECTIONS.md`, and `INTEGRATION_STATUS.md`.
Customers never supply developer keys. Store credentials only in service secret
settings. Keep public-sales/release attestations false until independently
verified; installing this image does not enable paid sales.

Enable `AUTOPOST_CLIP_ENABLED=true` only after checking storage, capacity and the
real upload → transcription → render → preview → download flow. `ffmpeg`,
`ffprobe` and optional Python engine dependencies are installed by this image.
The engine's first transcription also needs its configured speech-model download
from the model provider, or an operator-provisioned verified model cache. Cached
model files and live speech accuracy were not validated by fixture-transcript
rendering checks. Keep hosting egress permitted for required provider services.

The hosted studio accepts uploaded videos only, not arbitrary links or server
file paths. Video processing is limited to three attempts per workspace per UTC
day, two pending jobs per workspace and one render at a time. The initial upload
limit is 200 MiB. Review these fixed preview limits before commercial packaging.

The source includes a manual test-client packaging workflow, not signed store
releases. Supply this web service's **real** HTTPS origin to that workflow. No
Render hostname is invented or bundled into native clients.

Operator acceptance:

1. Open `/welcome`, `/install`, `/account` and the public example clip over HTTPS.
2. Register two separate customers and deliver actual verification/reset emails.
3. Enable rendering, upload an authorized spoken video, wait for its genuine
   transcription/render, play the full clip, correct a caption and render again.
4. Confirm the other customer cannot list or download those files. Delete the
   clip and verify the source/output files are gone.
5. Connect approved customer Pages/editors and verify each advertised action
   using authorized test accounts. Clip downloads do **not** publish as social
   video; no video-capable customer publisher is delivered by this release.
6. Confirm the website is deployed from the pushed branch before distributing
   any native wrapper. Test real devices, uploads, OAuth and billing separately.

Creating a Facebook/Instagram/YouTube business account or Page remains a step on
that platform. The app does not automatically create social accounts, fetch live
editing trends, promise views/earnings, or guarantee an app-store approval.
