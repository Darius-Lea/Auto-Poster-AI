# Auto Post privacy policy

Effective date: October 29, 2026

Auto Post is the publishing tool used by the operator of **FORT WAYNE GONE
WILD** to prepare and publish public-records content to its Facebook Page.
This policy describes the current operator-run tool, rather than a public
subscription service.

## Information the tool processes

- Facebook authorization credentials, the selected Page's name and ID, and
  identifiers and captions of content created through the tool. Facebook login
  and consent are handled by Meta. The API publishing tool uses an access token,
  rather than collecting the operator's Facebook password.
- Public source information, including names, booking photographs, booking
  dates, ages when available, listed charges, source links, and roster status.
  Wanted-source processing is available only when configured and enabled.
- Operational records, such as publishing results, errors, post and photo IDs,
  source observations, and previous delivery records used to prevent repeats
  and update existing posts.
- Information a person voluntarily sends to the Page operator when requesting
  a correction, asking a question, or requesting deletion.

The current application does not use its granted Page permissions to collect
private Facebook messages or build profiles of Page visitors. It does not
implement advertising tracking or a visitor analytics service.

## Purposes and recipients

Information is used to prepare factual captions and branded images, publish to
the authorized Page, prevent duplicate posts, monitor source changes, maintain
delivery history, troubleshoot failures, and handle requests.

Published captions and photographs are public on Facebook, subject to Meta's
visibility settings and policies. Facebook processes authorized requests and
published content under its own terms and privacy policy.

GitHub Actions runs the tool and can store operational logs, delivery databases,
cache checkpoints, and recovery artifacts. With Google Gemini enabled, public
record details are sent to Google to generate captions. With Telegram
notifications enabled, run summaries and selected booking photographs and
captions are sent to the operator's configured Telegram chat. Other publishing
backends receive content only if the operator configures and selects them.
These providers process information under their own terms and policies.

## Storage and retention

API credentials are configured as deployment secrets. Publishing history and
status observations are stored in a SQLite database and deployment storage.
Recovery artifacts are configured for 30-day retention; caches and logs are
also subject to GitHub's retention, access, and eviction rules.

Publishing history and published posts may be retained to preserve the original
record and support later corrections or status updates. Disappearance from a
source roster does not automatically delete an existing post. Retention is
also affected by provider settings, technical requirements, and applicable law.
The application does not automatically remove a provider's independent copies
or backups.

## Authorization, corrections, and deletion requests

An authorizing Facebook user can remove Auto Post's access through Facebook's
app or business-integration settings. Revocation prevents future authorized
API access; it does not automatically erase existing posts or local history.

For privacy questions, access requests, corrections, or deletion requests,
contact the operator through the **Message** option on the
[FORT WAYNE GONE WILD Facebook Page](https://www.facebook.com/1484443048079794).
Identify the relevant post or authorization and explain the request. Do not
send passwords, access tokens, or unnecessary sensitive documents.

Requests require manual review by the Page operator. The operator may need to
verify the request and review applicable public-record, retention, and legal
requirements. Where appropriate, the operator can remove associated app
credentials or stored data and correct or delete content it controls. Removing
an app authorization is separate from requesting removal of public-record
content. This policy does not promise automatic deletion or a particular
decision on a request.

## Changes

The operator can update this policy when the tool or its data practices change.
The effective date above identifies this version.
