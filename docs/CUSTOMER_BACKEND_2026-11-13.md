# Customer backend and creative workbench — November 13, 2026

This release connects the public four-step setup to customer registration/sign-in, authenticated workspace storage and real backend post preparation. The last step has a visible Continue to your account action, clamps navigation to the four valid steps and automatically imports the saved setup after the user explicitly continues through authentication. The imported plan can become an internal draft or inactive posts for explicitly selected authorized Facebook Pages. Repeated preparation reuses inactive rows; it does not duplicate publication or auto-activate on payment. Inactive posts can be given a new local posting time before activation.

## Accounts, destinations and plans

Customer credentials use server-side scrypt hashes, session identity, CSRF and bounded authentication attempts. Posts, series, imports, settings, Page connections, billing and worker jobs are scoped to that authenticated workspace. Existing data migrates to the owner workspace. Owner environment Page credentials are never used for customer jobs. Facebook authorization lists managed Pages, and the customer explicitly selects destinations. Each selected Page receives its own delivery record.

Basic is server-enforced: one connected posting Page/account per platform, and one attempted post per platform per UTC calendar day. Different supported platforms may be selected together; this does not add publishing connectors for unimplemented networks. Configuring two daily posts or multiple Facebook Pages requires an eligible higher plan. Daily allowance reservations are atomic and remain held after uncertain outcomes to avoid duplicate uploads. Scheduled posts, explicit activation, automations and delivery all enforce the policy. The existing owner workspace is exempt so the owner's established publisher is preserved.

## Creative automation

`/studio` provides per-workspace brand name, writing tone, location, hashtags and content instructions. Customers can prepare their own recurring text or explicitly authorized RSS/Atom/JSON Feed sources, choose Pages, cadence and approval. Saved automations are paused until started; edits pause them again. The worker checks one due automation at a time, deduplicates source items per Page, baselines older items on the first feed check and preserves per-run history. Review mode creates inactive posts; auto mode creates scheduled jobs only after entitlement, authorization and plan checks. The delivery queue still performs its own final checks.

Feed transport is bounded, HTTPS-only, pins public DNS addresses, validates TLS and rejects private networks/redirects. It refuses proxy-only environments rather than bypassing their policies. This cloud's feed collection therefore requires fixture tests; a normal direct-network Render host can use the transport. This is a structured-feed connector, not a universal website/app scraper. The existing city-specific legacy publishing pipeline is separate and remains unchanged.

## Managed AI and measured reports

OpenAI, Anthropic and Gemini APIs are managed by the service operator; customers do not enter developer keys. The router falls back on quota/transient failures, respects cooldowns and returns to its preferred configured service afterwards. Personal ChatGPT/Claude subscription allowances do not authorize these APIs and are not consumed. Drafting and analysis have workspace request budgets. Live API calls were not tested.

`/reports` counts actual app-recorded deliveries and can collect Facebook post reactions, comments and shares using that workspace's authorized Page. Missing values remain unknown; comparisons and AI explanations require sufficient measured data. It does not invent reach, follower growth or claim that location hashtags guarantee local distribution. Explicit optimization opt-in allows measured reports to influence future writing guidance. It never silently changes destinations, timing or approval rules. Slow source/report passes run separately from the delivery scheduler.

Reports cover posts recorded by this web app. Existing deliveries from the separate legacy publisher database are not automatically imported.

## Editor connections and remaining gaps

`/connections` implements Canva's supported OAuth/PKCE, encrypted per-workspace access/refresh tokens, custom design creation, editor links, owned-design exports and export status. The operator must register/configure the Canva integration and its exact `/connections/canva/callback` URL once; customers then use the connection screen. This is creation/edit-in-Canva/export, not unrestricted AI mutation of Canva layouts. Preview mode prevents external editor calls.

CapCut and muse.ai are clearly labelled external handoffs. Caption/brand-guidance ZIP exports are real, but CapCut timeline editing, Muse account/library/analytics synchronization and consumer AI account OAuth are not implemented. Muse documentation was proxy-blocked; a supported account-authorization API must be verified before those capabilities can be connected. Arbitrary photo/video uploads and direct media publishing are not added by this text/automation release. Non-Facebook publishing connectors remain unfinished.

A platform operator still needs the existing Meta application registration/approved permissions and stable encryption secret, configured verified subscription billing, and optional managed AI/Canva service credentials. Those belong in server secrets, never customer forms. Customer email verification, automated recovery, production security/load review, physical-phone checks and store releases remain separate work. This is not a claim that the live Render deployment or all third-party integrations have been verified.

## Validation

Full offline regression suite: 322 tests passed. Fixtures cover workspace isolation, cross-account rejection, payment/activation, Page routing, Basic limits and concurrent quota reservations, source dedupe/SSRF checks, provider fallback/cooldown, analytics, Canva token/design/export isolation and creative route preview guards. No Facebook post, paid AI request, charge or live editor operation was sent during verification.

Actual Chromium checks passed on desktop and a phone-sized viewport: review → customer signup → authenticated auto-import → backend draft preparation; profile persistence; studio/connections/reports; brand assets and owner preview. No horizontal overflow or page JavaScript errors were observed. The repeatable checks are `scripts/qa/customer_flow_smoke.py` and `scripts/qa/brand_setup_smoke.py`. GitHub app checks run both alongside the full suite.

Deploy the same existing web-app branch and retain the existing database. SQLite migrations add workspace ownership; do not delete customer/owner data to update. Service configuration and the actual Render hostname must be inspected separately; this workspace has no private Render account connection.
