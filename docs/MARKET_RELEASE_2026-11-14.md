# Market release evidence — November 15, 2026


## Current integrated result

The final source passed **493 Python tests in 24.794 seconds** and all five
Chromium workflows at phone and desktop viewports: brand/setup, customer import,
idea-first planning, installation/privacy, and the combined customer clip flow.
The combined flow verifies signup, confirmation gating, private logo save/remove,
upload and background import, immutable branded caption revisions, explicit
Facebook video scheduling/confirmed-state handling, and returning to the same
account across tools. Media/model/provider transports in that browser fixture are
synthetic; it is not a live provider or fresh-transcription test.

Delivered code includes one customer account for posts, Clip Studio and saved
branding; private normalized logo uploads; background direct HTTPS video-file
imports; opt-in confined eligible progressive YouTube imports; reviewed Facebook
Page video scheduling with shared Basic text/video allowance; actual dated
YouTube popularity observations when its server-side provider is configured;
curated editing looks; and a verified-local-model gate plus operator
transcription/render doctor. Kick, protected/private/adaptive-only videos,
universal social posting and measured live editing-effect/audio rankings are not
delivered. Native signing/store publication remain separate.

Beginner entry now starts with a working no-account marketing plan whenever
clipping is unavailable. Customers see a shared email/password account instead
of an unknown owner password. Missing confirmation-mail setup offers working
plan/logo actions instead of a false confirmation link. No customer login or
private database is committed to GitHub. Runtime data requires the persistent
Render volume described below.

**Public paid release remains unverified.** The actual Render service/revision,
durable data, customer mail, fresh speech inference, provider authorization and
one explicitly approved video publication must still pass live acceptance. The
saved network draft has not been observed as applied to this workspace; public
Render/model requests were denied before reaching their services.

## Deployment identity and observed access

The user supplied the real public address:
`https://auto-poster-ai-free-preview.onrender.com`.
No hostname was inferred from a Render service name.

A read-only GET of `/welcome` was attempted with the existing proxy and TLS
settings. The normal command sandbox could not reach proxy port 8080. The same
request outside that sandbox returned **`CONNECT tunnel failed, response 403`**.
This is a network-policy denial before the site was reached. It is not evidence
that the Render service itself is down, outdated or misconfigured. No alternate
host/proxy route was used to bypass the denial, and no live app revision or
transcription result was obtained.

The current tools have no Render account/deploy connector or usable Render API
binding. A GitHub push is not a verified Render deployment. This environment's
credential inventory also does not establish what credentials Render contains.

## Shared-account release candidate

The customer app and Clip Studio share the same authenticated workspace. A new
customer creates their own email/password; the owner password is a separate,
optional legacy sign-in. Password hashes, workspace data and connection records
belong in the persistent private database, not the repository.

The release adds saved business-name/color/logo profiles and immutable job brand
snapshots; validated background video imports; a separate reviewed Facebook
video-delivery queue sharing Basic limits with text posts; and dated YouTube
popular-video inspiration with separately labelled caption presets. Speech
recognition fails closed until actual local weights pass the operator inference
check. These are code capabilities with local regression evidence, not proof
that the free-preview deployment has received the change or has its providers.

`render-app.yaml` is an opt-in separate Docker customer service with persistent
account/media/model storage. `Dockerfile.app` includes the speech verifier,
model-readiness module and bundled check sample. Optional
`AUTOPOST_CLIP_WARM_ON_START=true` downloads and verifies the official model
before starting the one-worker web process; it defaults off. The original
owner's live worker is unchanged. The proposed paid service/disk settings have
not been deployed, charged or performance-qualified by this task.

The deployment files were parsed and their expected persistent paths, opt-in
flags, required source copies and startup-shell syntax checked locally. That is
configuration validation, not an image build or Render schema/provider check.
See `HOSTED_APP_DEPLOYMENT.md` for the exact one-time operator setup and the
fresh-customer acceptance flow.

## Read-only deployment checker

After the development environment permits the supplied host, run:

```bash
.venv/bin/python scripts/qa/render_release_check.py \
  --origin https://auto-poster-ai-free-preview.onrender.com \
  --expected-commit FULL_40_CHARACTER_RELEASE_COMMIT
```

The checker makes bounded, nonredirecting GET requests only. It checks the
public welcome/install/registration pages, the manifest, `/version`, and an
unauthenticated rejection from `/clips/jobs`. It never signs in, queues work,
publishes, sends email, refreshes tokens or charges a customer. Response bodies,
cookies and credential values are not printed. An unknown deployment commit or
revision mismatch fails the revision check.

Even if every public check passes, private email, logo, import, fresh speech
transcription, publication and billing workflows remain `not_checked` until
their actual acceptance flows run. The checker always leaves
`ready_for_public_launch` false; a healthy public page is insufficient evidence.

Six checker tests pass. The first resumed full suite passed **410 tests in
15.545 seconds**: the previous 404-test integrated baseline plus these six new
diagnostic tests. Other feature agents were still implementing their changes at
that point, so this is an intermediate result, not the final integrated release
count. Final integrated validation must be recorded separately before a public release.

## Concrete remaining production work

1. Permit the exact Render hostname in this development environment's network
   settings, then rerun the public checker after a real deployment. A saved
   environment draft is not an applied network change.
2. Inspect the actual Render service's deployment branch and revision. The
   existing `render-mvp.yaml` installs only `requirements-app.txt`; it does not
   provision Clip Studio dependencies, FFmpeg or a validated speech model.
   `Dockerfile.app` is the separate customer/media image. Configure the selected
   service using the current hosted deployment instructions; do not replace the
   original owner's live publisher with a different entry point accidentally.
3. Attach durable database/media/model storage, ensure exactly one scheduler
   worker, and size CPU, memory and disk for the configured video limits. The
   older one-gigabyte starter-disk declaration is not a demonstrated capacity
   guarantee for uploads plus render intermediates. Exercise restart recovery
   and measure an actual representative job on the intended service tier.
4. Install or download the official speech model through the supported operator
   workflow. Obtain a fresh speech inference and finished render on the host;
   a supplied test transcript or package installation alone does not establish
   transcription readiness. Follow `TRANSCRIPTION_VERIFICATION.md` for the current speech helper.
5. Use a fresh customer to receive account confirmation/recovery email, return
   to Clip Studio, save a personal logo, upload or import a permitted video,
   wait for genuine transcription/rendering, preview it, correct captions and
   download the finished media. Confirm a second customer cannot access it.
6. Authorize a deliberately selected test Facebook Page, review the actual
   clip/caption/destination and explicitly schedule a test video. Verify the
   provider's final processing/publication state, Page identity, pause/cancel
   behavior and the shared Basic text/video allowance. A returned upload ID
   alone must not be presented as a published video.
7. Configure and verify any advertised live trend-data provider. Current
   popular-video metrics, dated creator news and curated editing presets are
   different evidence types. None proves that a specific edit, effect or audio
   is today's highest-engagement choice or guarantees earnings/views.

Operator/provider prerequisites still apply to public customer OAuth, billing,
support and store submissions as documented elsewhere. This file records the
actual access result and release gates, not a certification of market readiness.
