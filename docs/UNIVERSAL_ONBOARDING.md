# Auto Poster AI — universal beginner experience (product UX v1)

> Status: product design and browser-only concept; not a production service. No social networks, billing, AI, analytics or background jobs are connected in this prototype.

## Product positioning

**Your content, on autopilot.** Auto Poster AI is a category-agnostic publishing and performance platform for individuals, creators, local and online businesses, community groups, nonprofits, educators, publishers, and specialist content operators. **Never force a user to identify as a news publisher or select an industry**: the system should support a fully custom setup.

North-star outcome: a nontechnical person can create a workspace, connect eligible channels, choose what the system is permitted to post, set approval/autonomy preferences, then leave. Unattended work happens on durable servers, not a phone or GitHub Actions. App value comes from reliably saving time and improving outcomes, not dark patterns.

## Five-screen onboarding (under 5 minutes for common use cases)

1. **Welcome — what do you want to achieve?** Choices: grow an audience; keep a page active; promote my business; schedule my own content; automate updates from sources; manage clients; something else. Optional page name, optional business category and free-text custom goal. Do not gate on industry.
2. **Connect accounts — effortless and honest.** One-tap platform tiles. Redirect to each official OAuth/login/authorization screen, select the eligible Page/creator/business channel and requested permissions. Show connected identity, scopes, account status, token expiry/reconnect, revoke button. Never ask for a Facebook developer account, tokens, social passwords, or store raw passwords. Auto Poster AI maintains developer registrations centrally. Not all platforms/accounts permit API auto-posting; display exact capabilities per destination (draft/queue/direct publishing/analytics) and explain unsupported actions instead of pretending they work. Never promise literally every network.
3. **Choose content source — three paths.** (A) 'Make content with AI': prompt and/or approved brand assets; (B) 'Post my content': uploads, media library, import calendar, external storage; (C) 'Watch approved sources': RSS and licensed feeds, first-party website, authorized integrations and explicitly permitted collection. Allow hybrid sources. Source rights and attribution are first-class; no arbitrary web scraping as a default.
4. **Choose automation and style.** 'Schedule what I upload', 'AI drafts for approval', or 'Autopilot: publish permitted content based on my rules'. For each destination: time zone, posting windows, max posts/day, topics, exclusions, optional brand voice, media templates, duplicate filters. Preview example output. Require explicit acknowledgement before first autonomous run; scheduled manual uploads do not need reapproval if user authorized that schedule. Higher-risk categories (arrest allegations, mugshots, obituaries, missing people, health/financial claims, etc.) have source verification, safety, privacy, correction/removal and optional mandatory review; no automatic speculative/defamatory posts.
5. **You're ready — health and dashboard.** Show 'Autopilot on/off', next scheduled post, connection health, published / scheduled / awaiting approval / blocked counts, the latest posts with remote links, easy pause/stop, weekly summary and meaningful tips. Guide customers toward a first successful verified post.

## Dashboard IA

- **Home**: plain-English "Your pages are running" or "Needs attention", next post time, today's queue, quick Create/Upload/Pause.
- **Calendar**: multi-channel scheduling and media previews; show destination-specific formats.
- **Autopilot**: topic rules, trusted sources, active workflows, safety approvals, run history and exceptions.
- **Create**: reusable asset library, AI-assisted captions/video adaptations, templates, human edit and bulk import.
- **Insights**: per-channel reach, engagement, audience/follower changes, click conversions when instrumented, post/topic/time comparisons, sources and freshness; missing data is unknown (never zero). AI Coach offers evidence, sample sizes and reversible A/B experiments, with auto-apply separately opt-in.
- **Accounts**: connected identities, reconnect/disconnect, permission scope, platform capability labels.
- **Settings/Billing**: brand kit, timezone, alerts, export/delete account, privacy, subscription management, support.

## Growth coaching: practical, grounded, low noise

- Separate network-specific metrics; do not sum inherently different reach or unique-audience measures.
- Analyze results only after adequate samples: suggest 'collect more data' rather than inventing certainty.
- Each coaching recommendation states: what changed, sample size and period, expected tradeoff, one-click experiment, rollback.
- Post only through platform permissions and official APIs. Store raw metric snapshots and retrieval timestamps.
- Avoid "viral" promises; track real outcomes and time saved.
- Never silently escalate frequency or change brand voice, sources or sensitive-content approval requirements.

## Unattended architecture and operating model

- Native iOS / Android clients plus web dashboard are **controls**, not job runners.
- Server-side jobs in durable queue; Postgres, tenant isolation, per-channel OAuth credentials in encrypted token vault, secrets rotation, webhook/status refresh, retries with exponential backoff, rate limits, reconciliation of uncertain uploads, idempotency keys and audit logs.
- Collection adapters: RSS, licensed APIs, owners' websites, connected first-party sources. Respect robots/terms/copyright/privacy; avoid scraping behind authentication, login bypasses and credential automation.
- Publishing adapters per platform, feature flags for format and eligibility. Some destinations may offer export/manual handoff only pending developer approval; show accurate current support.
- Media storage/transcoding, metadata and rights, copyright reporting, moderation/correction handling.
- OAuth connection failures should not stop other channels. Alert only on actionable exceptions. 'Pause all' is always visible.
- Explicit user consent for each autopilot workflow, channel and content source. A useful preview should precede activation.
- Subscription entitlements and server-side quota enforcement. Introductory **Basic at US$10.99/month** (owner confirmed); other prices and exact limits pending unit-economics review. Native app digital-subscription billing should comply with Apple's applicable in-app-purchase rules; web flows and external links require policy review.

## App Store readiness checklist

Not App Store-ready until all are true:
- Real, usable iOS application (not just a web screenshot); polished onboarding and accessible components.
- OAuth client review and publishing permissions approved per platform; accurate capabilities shown.
- Verified billing receipt/server notifications and subscription restore/manage/cancel UX.
- Privacy policy, terms, account deletion, data export, purpose disclosures and rights to source material.
- Secure storage, rate limiting, abuse reporting, automated moderation and correction workflow for harmful/outdated data.
- Durable worker, health monitoring, audit trails, tenant isolation and billing limits.
- On-device + server automated tests, TestFlight QA and App Review submission assets.

## Retention through trust and utility

Users return because the app consistently publishes what they asked, explains what happened, shows the value and saves effort. Avoid forced engagement loops and hard-to-cancel subscriptions. Product promise is **"Set your rules once. Stay in control. Let the work continue."**

## Prototype testing

Open `prototype/onboarding.html` locally in any browser. It demonstrates copy, flow and visual hierarchy; all sample connections, metrics and publishing controls are clearly marked DEMO. It does not transmit data or perform publishing.
