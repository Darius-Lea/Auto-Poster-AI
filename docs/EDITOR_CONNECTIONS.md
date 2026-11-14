# Creative editor connections

The app can keep technical setup behind the service. Customers authorize an editor through its official account screen; they are not asked to create developer applications or paste API keys. This requires the Auto Poster operator to register and configure the service integration once. A visible link to another website is labelled as a handoff, never as an account connection.

## Canva: implemented Connect API flow

The connector uses Canva OAuth Authorization Code with PKCE (S256), a 10-minute state/nonce bound to the authenticated workspace, one-use nonce storage and a confidential server-side token exchange. Access and refresh tokens are encrypted separately for each workspace. Short-lived access tokens refresh using the stored refresh token. Disconnect removes local access even if Canva cannot confirm remote revocation; the user can also remove the integration in Canva settings.

Server settings (the operator supplies these in hosting secrets):

- `CANVA_CLIENT_ID`
- `CANVA_CLIENT_SECRET`
- `CANVA_REDIRECT_URI` — the exact public HTTPS callback registered in Canva, e.g. `https://app.example.com/connections/canva/callback`
- `AUTOPOST_CONNECTOR_SECRET` — a stable, random encryption secret of at least 32 characters. The existing `META_PAGES_ENCRYPTION_SECRET` is a supported fallback. Changing this key requires an explicit token migration or account reconnection.

Requested scopes are `design:content:read`, `design:content:write` and `design:meta:read`. Canva application review, the enabled scopes and the connected account's eligible plan still determine availability. Nothing in these local tests establishes public Canva app approval.

Supported operations:

1. Connect the customer's Canva account through Canva's own consent screen.
2. Create a named custom-size design, defaulting to 1080×1080, and open its real Canva edit/view link.
3. Keep the created design associated with that workspace.
4. Explicitly request a PNG, JPG, PDF or MP4 export, then poll its actual export-job status. Formats are subject to the design and account supporting them. Completed download links expire and are restricted to HTTPS Canva-owned hosts.
5. Disconnect and remove stored account credentials.

Mutation timeouts are not automatically retried: a design or export might have been created even if a response was lost. The interface should tell the user to check Canva before creating another one. No export is silently published to Facebook.

The Connect API is not a general remote-control interface for every Canva editing command. This implementation creates an editable design and exports edits made in Canva; it does not claim that the assistant can alter arbitrary layers, perform all visual edits, or insert any generated caption into any design. Canva's official source currently marks several design-generation/copy features as preview with restrictions on public application review. Those features are not silently enabled here.

Protocol verified against Canva's official repository:

- [Connect starter source, commit 726e8cb6](https://github.com/canva-sdks/canva-connect-api-starter-kit/tree/726e8cb6f4062a8bfdc60851f547c7a17a1bcd32)
- [OAuth authorization scopes/PKCE](https://github.com/canva-sdks/canva-connect-api-starter-kit/blob/726e8cb6f4062a8bfdc60851f547c7a17a1bcd32/demos/common/backend/services/auth.ts)
- [OpenAPI, OAuth/design/export request and response schemas](https://github.com/canva-sdks/canva-connect-api-starter-kit/blob/726e8cb6f4062a8bfdc60851f547c7a17a1bcd32/openapi/spec.yml)

The official custom-design request uses `type_and_asset` and `design_type: {type: custom, width, height}`. A guessed `social_media_post` preset is deliberately not used.

## Muse.ai

The requested service is [muse.ai](https://muse.ai/), the video platform. Access to its official API documentation at `https://muse.ai/api` was explicitly blocked by this environment's proxy (HTTP 403), so a customer OAuth/library/analytics API could not be verified. The UI can open Muse.ai as a clearly labelled external handoff. It does not claim that a personal Muse.ai account is connected, read its private library, obtain social-platform analytics, or consume Muse.ai as a general chat-model fallback. No shared owner Muse.ai library credential is exposed to unrelated customers.

A full connection needs a documented supported authorization path, scoped access to the particular customer's videos and any required provider registration. It remains unfinished. Do not substitute a guessed endpoint or browser-session scraping for that work.

## CapCut

A general public API for customer login and arbitrary timeline editing was not verified. The supported handoff is to download the app's content/caption package and use it in the [CapCut editor](https://www.capcut.com/). This is not a claimed CapCut project file, OAuth account connection, automatic render or automated timeline edit. An approved partner integration, if available for this service, is separate work.

## Verification boundaries

Eight offline connector tests passed for PKCE, confidential exchange, encrypted per-workspace tokens, state mismatch/expiry/replay rejection, token refresh/rotation, custom design schema, design/export ownership, safe export URLs, disconnect and uncertain mutation handling. HTTP responses are fixtures; no live Canva account was connected, no paid API action ran, and no external content was published by the tests.
