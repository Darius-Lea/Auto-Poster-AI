# First-time customer audit and release decision — November 13, 2026

## Decision

**Not approved for a public paid launch yet.** The reviewed baseline was `84fb8fa`. This release fixes concrete customer, publishing and billing defects and adds a useful idea-first journey. It is a tested release candidate, not evidence that the current Render deployment or live third-party services are ready. It must not be sold as a universal product builder, all-network publisher, automatic CapCut/Muse editor or guaranteed route to virality.

## Persona and task

The customer has a business idea, little social-media experience, no existing marketing copy and possibly no Facebook business Page. They dislike technical setup and want useful work completed before they purchase anything. The task is to describe the offer, develop a small first-version plan, review a week of marketing, save it, connect a supported Page and deliberately schedule approved posts. They must also be able to pause, recover access, manage billing and get help.

Before this review, the app's tests showed useful backend components, but the customer was still asked to choose among eight platforms and write marketing copy before receiving guidance. A primary automation route saved non-running records while real automation lived elsewhere. Customer settings included arrest-booking and GitHub controls, and several critical purchase/delivery behaviors were wrong.

## Findings and changes

| Priority | Customer-visible failure at baseline | Change and evidence |
| --- | --- | --- |
| P0 | A customer could select “pause” while ordinary queued posts still published. | Added a separate, workspace-wide publishing pause honored at claim and immediately before sending, by source workers and the legacy control bridge. Default preserves existing schedules. In-flight requests may finish. Regression tests prove paused jobs do not send. |
| P0 | Repeated checkout taps could open multiple checkout sessions, even for an existing subscriber. | Stable durable checkout idempotency and cached session links; active/nonterminal subscription guard and billing-portal path. Mock billing tests prove repeated requests do not create a second charge flow. |
| P0 | A same-second stale active webhook could reverse cancellation. | Conservative event ordering gives terminal cancellation precedence at an equal timestamp. Verified later events can update state. Regression tests cover the previous resurrection case. |
| P0 | Higher-tier offers existed without a functioning tier-selection path. | Only configured server-owned price IDs are offered. Signed price data determines entitlements; customer metadata cannot grant a higher plan. Public sales remain closed until service setup and operator sign-off. |
| P0 | Customers could not recover an account or confirm their email. | SMTP-backed reset and verification flows, hashed expiring one-use purpose-bound tokens, bounded requests, auth-version session revocation and password change. No public token disclosure or access-log URL token. Real delivery still needs testing. |
| P0 | Customer account lifecycle lacked export/deletion. | Authenticated workspace export excludes credentials. Password-confirmed deletion checks subscriptions, unresolved checkout, sending jobs and automation leases before removing customer content. Actual financial reconciliation is retained where necessary. |
| P0 | Legacy owner controls and two automation models confused customers. | Owner-only controls are hidden for customers; customer automation links go to the runnable studio. Settings expose the genuine pause, account and billing actions. |
| P1 | An idea-only user had to arrive with finished copy and a strategy. | `/launch` builds a free editable offer worksheet and seven starting drafts from supplied answers. It gives product-validation steps without pretending to manufacture a product or research demand. Account creation comes after the first useful result. |
| P1 | Saving a week required repeated manual work and risked accidental duplicates. | Authenticated, idempotent seven-draft import; reviewed single-Page seven-post scheduling with local timezone, future dates, entitlement, email confirmation and Basic quota checks. Changes to already-scheduled posts are rejected rather than silently dropped. Fixtures prove no duplicate activation. |
| P1 | Working captions could disappear while navigating to an editor or connection. | Workspace-scoped autosave/restore with visible status and revision checks; stale tabs cannot silently overwrite a newer caption. Drafts have an explicit Use this draft path. |
| P1 | An internal-draft backlog could delay paid external jobs. | External deliveries are prioritized; scheduled-draft backlog is bounded and customer scheduling is rate-limited. Tests cover the backlog case. Load testing remains necessary. |
| P1 | Missing share counts could prevent useful reporting forever. | Compare a consistent shared metric set across at least three posts; reactions/comments can be used when shares are unavailable. Missing values remain unknown, and AI receives the exact comparison basis. |
| P1 | A customer with no business Page, a delivery problem or a forgotten password had no useful next action. | Public help explains Page creation/connection, delivery uncertainty and recovery. Customer status links point to connection/history/help. Privacy, service information and account controls are linked. |
| P1 | Future integrations, unusual cadences and owner-only wording could look like working customer features. | Future platform labels are explicit; feed configuration is an advanced disclosure; the main path starts with an idea or first post. CapCut/Muse remain labelled handoffs, and Canva's supported operations have explicit boundaries. |

Uncertain publication is deliberately held; a customer assertion alone does not free a Basic delivery allowance or prove that no post exists. The app provides Page/reconnection/help guidance, but automated remote reconciliation of every ambiguous delivery remains unfinished.

## Verified customer journeys

Actual Chromium walkthroughs passed on a 390×844 phone viewport and a 1440×1000 desktop viewport:

1. Open the website with only an idea → enter offer/audience details → receive seven editable drafts.
2. Edit a caption → create a customer account → restore the plan → save seven real backend internal drafts.
3. Open a saved draft → edit → navigate away after autosave → restore the working caption.
4. Pause and resume publishing → confirm customer settings do not expose owner booking/GitHub controls.
5. Open help, privacy, terms, account settings and recovery.
6. Existing four-step setup, owner preview, branding, manifest assets and per-workspace draft flow still pass.

The campaign activation and actual delivery-control paths are exercised with authenticated fixture accounts and mocked provider responses. No live Facebook publication, charge, SMTP email, paid AI generation or Canva operation was sent during this audit. Browser automation is not a substitute for observing representative human customers or testing physical iPhone/Android hardware.

Full regression suite: **363 passing tests**, including original publisher behavior and the added account, pause, billing, idea-plan, quota, analytics and isolation cases. Reproducible browser checks: `scripts/qa/brand_setup_smoke.py`, `customer_flow_smoke.py` and `novice_launch_smoke.py`. CI is configured to run them.

## Required before accepting public payments

1. Inspect the actual Render web deployment and exact app domain; verify the intended commit, stable cookies, durable storage, backups/restore, HTTPS and one-worker queue configuration. This workspace has no authenticated Render dashboard connection.
2. Complete a live customer Facebook authorization using a person outside owner-only app roles and the required platform approvals. Publish to an authorized test business Page, record the actual post ID, test expiry/reconnect and confirm Basic daily enforcement and pause in deployment.
3. Test Stripe in its test environment: displayed price/tier, one checkout under repeated taps, webhook signature/order/replay, renewal, failed payment, downgrade and cancellation. A public purchase flow must not be enabled by assumption.
4. Configure and test real verification/reset email, sender deliverability, public support contact, operator identity and production privacy/commercial policies. Test account recovery and deletion against the deployed service.
5. Test accessibility and the complete flow on physical phones, realistic network loss and expected concurrent customer load. Offline fixtures do not establish latency, uptime or capacity.
6. Advertise only the current supported scope. Photo/video uploads, media publishing, remaining social connectors, full Canva AI layout editing and CapCut/Muse account editing/synchronization are not delivered by this candidate. App-store binaries and store review are separate work.

`/readiness` is owner-only and reports configuration/attestation flags without secrets. It does **not** independently certify market readiness. Customer checkout defaults closed and also requires the configuration checks plus explicit operator release/legal/durability attestations. Do not set those flags until the corresponding real checks are complete.
