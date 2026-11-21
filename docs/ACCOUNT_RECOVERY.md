# Customer account recovery and self-service

Customers can use `/account/forgot` and `/account/reset` for a one-use, 30-minute password reset. Signed-in customers use `/account/settings` to change their password, export their data or request deletion. These controls manage customer accounts; the separate owner service password remains an operator setting.

## Operator setup

Recovery requires server configuration, not customer keys:

- `AUTOPOST_PUBLIC_URL`: the canonical public HTTPS origin, with no path, query, fragment or embedded credentials. Host headers do not choose reset destinations.
- `AUTOPOST_SMTP_HOST` and `AUTOPOST_SMTP_PORT` (default 587).
- `AUTOPOST_SMTP_FROM`: a valid sender email address authorized for that service.
- `AUTOPOST_SMTP_SECURITY`: `starttls` (default) or `ssl`; unencrypted mail transport is not supported.
- `AUTOPOST_SMTP_USER` and `AUTOPOST_SMTP_PASSWORD` together, if the provider requires authentication.

`account_support.mail_configured()` reports syntax/configuration readiness without revealing secrets. It does not verify a real message was delivered. If configuration is absent, the page clearly says recovery email is unavailable and does not pretend it sent an email. No live message was sent by implementation or tests.

For configured recovery, known and unknown emails receive the same public response. Tokens are stored as SHA-256 hashes, expire after 30 minutes, and are invalidated on use or when a replacement is issued. The email link carries its token in a URL fragment; the reset page removes that fragment before submitting it through POST, so the token is absent from normal HTTP URL/access logs. Responses use no-store and no-referrer headers. Password reset and password change increment `auth_version`; middleware must reject signed sessions with an older version. Tokens, passwords, addresses and upstream credential text are not printed by the recovery failure logger.

CSRF and bounded attempts apply. Email delivery failure gives the same generic public response as an unknown account to avoid revealing registered addresses; the operator receives a generic log warning. Real delivery/reliability and abuse testing remain deployment checks. Registration email ownership is now confirmed separately through one-use verification links; drafts remain accessible while confirmation is pending.

## Export and deletion

The export contains only the authenticated customer's account profile, posts, plans, Page display identities and selected preferences. It excludes password hashes, authentication versions, encrypted tokens and editor connection credentials. It does not export another workspace or the owner's publishing database.

Deletion requires the current password plus typing `DELETE`. It refuses active or unconfirmed billing states, an open or unconfirmed checkout attempt, a sending post, or an in-flight creative automation. Customers must first cancel subscriptions in Billing and receive cancellation confirmation. A local checkout timer does not prove payment was never taken: a saved checkout is verified through a read-only fixed Stripe endpoint before deletion. It must be expired/unpaid without a subscription, or reference the same verified canceled subscription already stored. Missing credentials, provider failures, a different customer, or unknown payment state fail closed; no payment/cancellation action is sent. The attempt identity is rechecked inside the deletion transaction to reject concurrent checkout changes. The app does not silently cancel payment subscriptions or delete already-published social content.

After those checks, customer workspace rows, connection credentials, reset tokens and the login account are removed transactionally. Other customer and owner workspaces remain. A signed subscription/payment-state record may be retained for billing support, without the removed account profile/content. A real public service still needs an operator-approved retention policy; this code does not invent one. Provider accounts, published posts and external data must be managed with those providers separately.

Background jobs must continue checking workspace/account/connection validity before saving new content or publishing. The deletion gate blocks in-flight creative publishing, but provider requests and production race/load conditions still require live validation.

## Verification

Offline tests cover hashed/expired/single-use tokens, generic recovery responses, missing SMTP configuration, mail failure redaction, CSRF, password/session revocation, export isolation, password-protected deletion, active subscription and pending checkout guards, in-flight publication rejection and retention of another customer's data. SMTP delivery is mocked; no external email or payment action occurs.

## Email ownership confirmation

New and migrated customer accounts begin with `email_verified=false`; the separate legacy owner is unaffected. Signed-in customers request a 30-minute link at `/account/send-verification`. Requests are limited to three per account and per IP in ten minutes. Tokens are hashed in a distinct table, bound to the current account email, removed after use and never accepted as password-reset tokens. The `/account/verify` page requires an explicit POST confirmation, so merely fetching an email link does not activate the account. The fragment token is stripped from browser history before submission. Confirmation does not change the password, grant a session, change subscription state or publish anything.

The web application must gate customer external connections, publication activation and checkout on the verified state while keeping drafts available. Unconfigured SMTP is shown honestly and disables sending; no live verification email was sent during tests. Verification is not proof of a business identity or social-platform authority.
