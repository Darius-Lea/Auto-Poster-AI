# Release verification — November 14, 2026

## Verdict and scope

The existing web customer journey passes its automated regressions. This is evidence for the code under test, **not** a claim that Render has deployed this revision, that customer OAuth is approved, that a live integration has succeeded, or that any app store has accepted an application. Public paid launch still requires the live checks below.

The initial reviewed commit was `49a28ee` on `feature/saas-appstore-foundation-2026-11`. The initial 363-test baseline was followed by integration of Clip Studio from `origin/feature/clip-studio` (`539b5f1`), the private hosted bridge, PWA, native client sources and release automation. The final integrated suite passed **404 tests in 19.246 seconds**. All four browser scripts passed against the integrated app after updating the brand script to use the renamed text-post link; the customer-flow, novice-plan and install/privacy checks also passed. Native configuration/helper tests passed separately (5 release, 3 mobile, 7 desktop); native installers were not built. The original owner Facebook publisher remains a separate deployment path; its existing operation is not proof of customer connector readiness.

## Checks completed

| Check | Actual result | Evidence and limits |
| --- | --- | --- |
| Python regression suite | **404 passed**, 19.246 seconds | `.venv/bin/python -m unittest discover -s tests -v`; retained local log `/tmp/autoposter-final-integrated-2026-11-14.log`. External services are mocked where applicable. |
| Public brand and four-step setup | Passed on Chromium, phone 390×844 and desktop 1440×1000 | `scripts/qa/brand_setup_smoke.py`; public assets/manifest icons, landing, setup completion, draft persistence and owner preview. No worker started. |
| Customer account and creative workspace | Passed on both viewports | `scripts/qa/customer_flow_smoke.py`; reviewed setup → customer registration → scoped automatic import → real prepared draft; profile persistence; studio, connections and reports. |
| Idea-only novice journey | Passed on both viewports | `scripts/qa/novice_launch_smoke.py`; idea → seven editable captions → registration → seven real saved backend drafts; edit/autosave/restore; pause/resume; customer settings; help, privacy, terms and recovery. |
| Install and offline privacy | Passed on both viewports | `scripts/qa/install_clip_smoke.py`: root worker, exactly five public cached paths, private pages and APIs stay uncached, logout rejects anonymous jobs, actual server-down fallback. No render worker started. |
| Browser errors/layout | No JavaScript page errors or horizontal overflow detected by those scripts | Automated Chromium viewports do not establish physical-device behavior, accessibility conformance or real-network performance. |
| Privacy/account controls | Existing fixture regressions passed | Tenant-scoped exports exclude connection secrets; password reset/change revokes old sessions; tokens expire, are single-use and purpose-bound; deletion guards unresolved billing and in-flight publishing. This is not an independent security audit. |
| Publishing/billing safety | Existing fixture regressions passed | Workspace pause, Basic Page/daily limits, uncertain-delivery holding, checkout idempotency, server-selected prices and conservative webhook ordering. No actual publication or payment was made. |

The browser scripts use temporary synthetic accounts/databases and `AUTOPOST_PREVIEW_MODE=true`. Their initial default-sandbox invocation could not start a loopback server; the same scripts succeeded after an authorized execution escalation. Screenshots are stored outside the repository under `/workspace/artifacts/`.

## Clip Studio integration checks added after the baseline

The authenticated `/clips` bridge has **12 focused passing tests** in `tests/test_clip_web.py`. They cover private workspace ownership, authentication/CSRF, verified-email and preview guards, source/time/style validation, asynchronous queueing, daily/pending limits, refunds for invalid pre-queue uploads, source-length adjustment for automatic clips, strict artifact readiness, private downloads, per-job business-name/color kits, caption revisions reusing the original transcript/window, interrupted-job holding, and account deletion/file cleanup. Render failures retain their allowance and never claim publication. A customer account must still exist and be verified inside the atomic queue reservation, closing an account-deletion race.

A separate temporary Chromium fixture walkthrough passed on phone and desktop: register → private upload → asynchronous completion → load caption words → correct words → queue a revised clip. It detected no JavaScript errors or horizontal overflow. Those browser checks mock video probing/rendering; screenshots `autoposter-clips-phone.png` and `autoposter-clips-desktop.png` are local artifacts, not live deployment evidence.

Uploaded media probing restricts both protocols and demuxers **before parsing**. An independent actual-FFmpeg check confirmed that a DASH manifest disguised as `source.mp4` is rejected while a genuine H.264/AAC MP4 is accepted. This prevents a file extension alone from enabling manifest reads of other local resources. See [CLIP_STUDIO_INTEGRATION.md](CLIP_STUDIO_INTEGRATION.md) for the adapter's real short render and corrected-caption fixture evidence; those transcript-reuse checks do not establish a fresh Whisper-model transcription or live model download.

Clip Studio remains opt-in through `AUTOPOST_CLIP_ENABLED`. Its files are private downloads, not automatic video publication through the existing text/photo publisher. The default UI uses automatic moment selection and hides advanced settings; new unverified accounts see a direct email-confirmation action before they can upload. Suggested moments use transcript signals, not measured future engagement or a verified live trend feed.

## Connection capability boundaries observed in source

| Service | Implemented behavior | What this verification does not establish |
| --- | --- | --- |
| Facebook | Official authorization, selected owned business Pages, encrypted Page credentials, workspace-scoped text publishing and supported post metrics | Public app review/permissions, a non-owner customer login, live test-Page publication, token expiry/reconnect and metrics under deployed credentials |
| Canva | OAuth/PKCE, encrypted/refreshable customer tokens, create/open an authorized design, export and poll owned exports | Successful live authorization, approved service registration, supported exports in a real account, or automatic AI editing of arbitrary layouts |
| OpenAI, Anthropic, Gemini | Managed backend drafting with bounded availability/quota fallback and cooldowns | Valid live API credentials, paid quotas/model availability or acceptable deployed response latency; consumer chat subscriptions are separate |
| CapCut | External editor and caption/brand-pack handoff | OAuth login, native project generation, automatic timeline editing or rendering |
| muse.ai | Explicit external library handoff | Customer account synchronization, private media/analytics retrieval or automatic video editing |
| Other social networks/custom websites | Saved plans/connection requests with unavailable status | A selected destination is not a publishing connector; no universal cross-platform posting claim is justified |
| Stripe and SMTP | Billing and recovery/verification code covered with fixtures | Actual checkout/webhooks or deliverability under the operator's deployed configuration |

Adding a connector to the development assistant does not by itself register an OAuth application, grant Auto Poster's customers access, or configure the running Render service. Configuration-presence checks must remain distinct from endpoint reachability and authenticated account verification.

## Live release gates still outstanding

1. Identify and inspect the exact Render service, branch/revision and public HTTPS domain. Confirm stable sessions, durable database storage/backups, queue topology and restart behavior.
2. Connect a test customer outside owner-only provider roles. Authorize a chosen Facebook test Page, publish one deliberately approved test post, retain its provider ID, test revocation/reconnect, and confirm Basic/pause behavior in deployment.
3. Complete live Canva authorization/create/edit/export with the operator's registered integration. Validate actual managed AI services and fallback without claiming unsupported consumer-account credit sharing.
4. Run Stripe test-mode lifecycle scenarios, including repeated checkout, signed/replayed/reordered webhook events, renewal/failure/cancellation and account-deletion reconciliation. Configure and verify actual confirmation/reset email delivery.
5. Complete service identity, public support, production policies, durable-storage and live-release attestations. Customer sales must remain closed until the actual requirements are met; attestation flags are not independent certification.
6. Validate installation/native clients on physical iPhone/Android/Windows devices, official sign-in flows and realistic network interruptions. Native store signing, developer accounts, permitted subscription billing and review are separate release requirements.
7. Exercise representative concurrent usage and real response latency. Passing fixture tests does not prove throughput, uptime, meeting/office features or universal third-party integrations.

Refer to `MARKET_READINESS_2026-11-13.md` for the broader novice audit. No new store listing, signed release, live provider mutation, paid AI request, charge or email delivery was performed by this verification task.
