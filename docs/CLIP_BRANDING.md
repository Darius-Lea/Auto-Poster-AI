# Customer logo and saved Clip Studio look

Customers can save a business/creator name, an accent color and an optional logo
in the Clip Studio **Your business look** card. Supported uploads are still PNG,
JPEG or WebP files up to 4 MiB, at least 16 pixels on each side, at most 8192
pixels on either side and at most 16 million source pixels. Files must match
their decoded format, extension and MIME type. SVG, animated images, URLs and
customer filesystem paths are not accepted.

The server verifies and decodes the image, applies its orientation, converts to
RGBA, fits it within 2048 pixels and writes a fresh PNG from pixels. A transparent
square border preserves the logo's aspect ratio when the renderer places it on
its closing card. Original metadata is discarded; transparent PNG backgrounds
are retained. Pillow's decompression-bomb errors/warnings are rejected before
expensive decoding.

Brand settings use the same authenticated workspace as clips. The private API is
`GET/POST /clips/brand`; the current logo is served at `GET /clips/brand/logo` with
`Cache-Control: no-store`. A CSRF token and the current revision are required
for changes. An outdated revision returns 409 rather than silently overwriting
another tab. Customer account existence is checked inside the write transaction,
so a request racing account deletion cannot recreate an orphan profile.

Normalized logos are stored under the configured `AUTOPOST_CLIP_DATA_DIR` (or
the database directory's `clip-data`) in a workspace-hashed brands directory.
Their bytes are content-addressed. Reads, saves and queue snapshots coordinate
through SQLite's write lock; retired profile blobs are cleaned after a successful
save. A job receives its own copy before it becomes queued. Changing or removing
the current profile logo does not change an already queued clip.

Application integration:

- Register `branding_bp`; call `clip_branding.init()` during database setup.
- Before the global CSRF form parser, allow 4 MiB + 64 KiB **only** for the brand
  endpoint. Ordinary caption/account requests keep their smaller request limits.
- Include `templates/_clip_branding.html` in Clip Studio.
- Before marking a render queued, call
  `snapshot_brand(workspace, jobfolder, name=..., accent=...)`. It writes
  `jobfolder/brand/kit.json` plus an optional `logo.png`.
- The worker uses that saved kit. Caption revisions copy the parent `brand/`
  directory so the original look is retained; they must not take a new snapshot.
- Account exports can include the profile's name, accent and revision. The image
  can be retrieved through the private logo endpoint. Account deletion must call
  `delete_workspace(workspace)` **after** its own SQLite transaction commits to
  remove profile image files. That helper opens its own write transaction.

The logo is used by Clip Studio's closing card; the chosen accent is also used
by supported captions and visual elements. This does not upload a profile image
to Facebook, create a social business Page or claim a complete identity service.

Validation: `python -m unittest discover -s tests -p test_clip_branding.py -v`.
Eleven focused tests passed. They exercise actual raster normalization and the renderer's real
Pillow closing-card composition, along with access, CSRF, metadata removal,
format/size limits, revision conflicts, immutable snapshots and scoped cleanup.
No customer video, live account or external provider call is needed.

A separate temporary Chromium UI fixture passed at 390×844 and 1440×1000:
upload → private preview → save → reload retained settings → remove logo. It
also verified that saved names/colors update the clip form, with no JavaScript
errors or horizontal overflow. Screenshots are local artifacts
`/workspace/artifacts/autoposter-logo-phone.png` and
`/workspace/artifacts/autoposter-logo-desktop.png`. This is evidence for the
logo interface, not a live Render deployment or complete customer video flow.
