# Facebook multi-Page login: customer experience and release plan

## Customer journey

1. Sign in to Auto Poster AI.
2. Go to Facebook Pages and select "Connect with Facebook".
3. Facebook asks which eligible business Pages and permissions to share.
4. Select the Page(s) to connect; the app only activates Pages with permission to create content.
5. In the composer, select the exact intended Page or multiple Pages; each creates a separate job.
6. Remove any connection to stop its queued posts and recurring series.

Customers never create a Meta developer app, provide API keys, store Facebook passwords in Auto Poster AI, or see the Page token. Auto Poster AI owns the approved Meta app.

## Architecture

Official Facebook Login authorizes the service; the server checks a one-time OAuth state, exchanges a code for a user token, extends it, and fetches /me/accounts?fields=id,name,tasks,access_token with pagination. A Page access token belongs to the selected Page and stays encrypted on the server.

A selected Page requires CREATE_CONTENT in its Page tasks. Every outbound job includes workspace_id, page_id, platform, content, approval and subscription status, and per-destination remote result. Never use the first Page returned by Meta as an implicit default.

New module meta_accounts.py contains service-owned OAuth, server-only exchange, encrypted pending choices, Page activation/disconnection, workspace-scoped token lookup and per-Page publishing through core/facebook.py. The app_mvp.py starter now has the Page browser and composer routing. See tests/test_meta_accounts.py for offline tests.

## Required settings owned by Auto Poster AI, not customers

META_APP_ID
META_APP_SECRET
META_OAUTH_REDIRECT_URI (registered HTTPS callback, such as https://your-domain.example/facebook/callback)
META_GRAPH_VERSION
META_PAGES_ENCRYPTION_SECRET (stable random 32+ character secret; rotate only with token migration)
META_FACEBOOK_LOGIN_CONFIG_ID (optional Business Login configuration)

Keep these values in the host's secrets manager. Never put credentials in the app binary, frontend, mobile local storage, logs, repository or customer-facing forms.

## Meta permissions and approvals

Initial Page publishing typically needs pages_show_list, pages_read_engagement and pages_manage_posts, plus Page CREATE_CONTENT task. Add read_insights when analytics is built and pages_read_user_content and pages_manage_engagement for reading and replying to comments when Meta authorizes the use case. Check the latest Meta developer permission dependencies and API capabilities before submitting.

Production access for arbitrary customers requires Meta's relevant App Review, Advanced Access where required, and business verification if applicable. App role-only access in development mode is not a commercial release.

## Critical boundary

This is a secure multi-Page foundation in the existing single-owner mobile starter, not yet a tenant-ready SaaS. The shared owner password is not acceptable for unrelated customers. Before public release, replace it with customer accounts, authenticated workspace membership on every route, tenant-aware durable Postgres and workers, encrypted token vault, audit logs, rate limits, quota checks, account deletion and secure reauthorization. The Page storage functions accept workspace IDs; the current starter supplies only an owner workspace.

For sensitive news, arrests, missing persons, crime allegations and release-status updates, require verified sources, factual captions, explicit approval rules and a correction/removal process.

## Release tests

- Authorize two Pages with the same Facebook sign-in.
- Verify eligible Page choices without showing access tokens.
- Post to Page A only and verify Page B remains unchanged.
- Post to both Pages and verify separate destination delivery IDs.
- Disconnect Page A; verify its queued and recurring jobs are held or disabled, Page B unaffected.
- Test revoked permission, ambiguous API response, retry reconciliation and expired paid subscription.
- Complete Meta app approvals and use staging before enabling customer signups.
