# One account for Auto Poster AI and Clip Studio

Customers create their own account at `/account?mode=register`. Existing
customers use `/account?mode=login`. Both routes are visible from the public
website, and the registration page explains that posting, Clip Studio and saved
business branding use the same account. `next=clips` returns to Clip Studio after
registration or sign-in; it does not create a second identity.

No customer needs the operator's owner password. The optional `/login` route is
reserved for the original owner workspace and is no longer linked from the
normal customer account form. Production WSGI startup accepts an absent owner
password; when one is configured, it must contain at least 12 characters. A
stable, unpredictable `AUTOPOST_WEB_SECRET` of at least 32 characters remains
required for signed sessions.

## Where the accounts are saved

Account names, emails, salted scrypt password hashes and workspace identifiers
are stored in the private SQLite database selected by `AUTOPOST_WEB_DB`.
Passwords are not saved as readable text. Account records do not belong in Git,
the public website or a distributed desktop/mobile installer. Database files are
already excluded by the repository's `.gitignore`.

Use a persistent database location on the actual hosted service. `Dockerfile.app`
uses `/var/lib/autoposter/app.db`; the older starter blueprint uses
`/var/data/autopost_web.db`. The matching directory must be a persistent Render
disk or an equivalently durable private store. A temporary directory or an
ephemeral free service can lose accounts after a restart or redeploy. The source
cannot make an unmounted path persistent. Back up the database and private media
and keep one WSGI worker/instance until the service moves to a shared database and
external job queue.

Email confirmation and password recovery need the operator's working SMTP
configuration. Draft preparation and brand settings share the account before
verification; video rendering and external posting keep their existing email,
service-readiness, authorization and subscription requirements. A social-platform
connection is a separate official permission flow, not another Auto Poster
password. Customers never enter developer keys.

## Verified locally

`tests/test_shared_account_journey.py` covers registration without an owner
password, logout and fresh-client sign-in, the same customer retrieving saved
post drafts and branding in both work areas, isolation from another customer,
salted password storage and WSGI configuration guards. Test data is temporary and
synthetic; no real mail or provider action is sent. Live Render account retention,
email delivery and deployed revision still require verification on that service.

A temporary Chromium walkthrough also passed at 390, 760 and 1440 pixel widths:
public create-account navigation, registration, password show/hide, access to
both work areas, logout and returning sign-in to Clip Studio. No JavaScript
errors or horizontal overflow were observed on the owned account/home screens.
Screenshots are stored outside the repository under `/workspace/artifacts/`.

The public homepage now checks whether clip creation is available before offering
it as the primary start. When it is unavailable, the primary action is the
working, no-account first-plan builder, with a finished video example alongside
it. When clip creation is ready, it offers the first-clip flow. Customers see a
plain preparation message rather than model or operator setup instructions.
The account form explains the password length and the need for an accessible
email address before video verification, so these requirements are not surprises
after registration.
