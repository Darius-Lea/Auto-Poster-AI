# Auto Poster AI — Signal Fold

Original application identity created November 12, 2026. A forward-slanted P combines a post/card silhouette with a folded lower edge. The sky-blue signal dot gives the mark a recognizable accent at small sizes. The application mark is separate from the city/customer watermarks already used by the publishing pipeline.

## Palette

- Midnight navy: `#0B1424`
- Signal lime: `#C8F68A`
- Sky blue: `#89D8F5`
- Light text: `#F3F7FC`

Use lime for the primary mark and main action. Keep blue as an accent. The transparent lime mark belongs on dark backgrounds; use the navy app-icon backplate on pale backgrounds. Wordmarks are supplied for dark and light surfaces. Keep surrounding space at least as wide as the blue dot and preserve the proportions.

## Assets in `static/brand/`

- `mark.svg`: scalable transparent symbol for application headers.
- `app-icon.svg`: navy-backed icon; served by the existing `/icon.svg` route.
- `wordmark-dark.svg`, `wordmark-light.svg`: symbol and Auto Poster AI name for dark/light surfaces.
- `app-icon-1024.png`: large PNG export.
- `apple-touch-icon.png`: 180×180 iPhone home-screen icon.
- `icon-192.png`, `icon-512.png`: browser install icons.
- `maskable.svg`, `icon-maskable-512.png`: safe-area install icon with full background.
- `favicon-32.png`: small raster favicon.
- `publishing-orbit.png`: matching AI-generated website artwork made with the original mark as reference. Decorative concept art; it does not show live analytics or real scheduled work.
- `social-card.svg`, `social-card.png`: editable and 1200×630 sharing graphic.
- `empty-posts.svg`: empty-state illustration.
- `brand.css`: shared image sizing and brand variables.

SVGs are editable vector sources. PNG icons and the social card were rendered directly from those SVG sources in Chromium. The original generated illustration remains in `/workspace/generated_images`; the application uses its own copy.

## Integration and checks

Branding is connected to the marketing website, public setup wizard, owner dashboard, automation setup and owner login. The dashboard empty state uses the matching illustration. The manifest includes PNG and maskable icons as well as SVG. These changes do not introduce a native iOS application, change paid publishing authorization or replace customer/city watermark assets.

Initial branding validation: all 31 existing public-onboarding, preview, unpaid-setup and web-starter tests passed. Actual desktop/mobile browser checks passed for loaded brand images, the four-step setup, final subscription disclosure, saved draft restoration, dashboard and automation pages. Anonymous asset/manifest requests succeeded, PNG dimensions matched the declared manifest sizes, and the login logo loaded. No horizontal overflow or page JavaScript errors were observed. No publishing worker or payment checkout was started.

The release source is on `feature/saas-appstore-foundation-2026-11`; its live Render deployment must be verified separately. Production social preview crawlers and physical iPhone installation have not been tested; the sharing card is supplied and linked by the website metadata.

Release validation also passed the complete 239-test regression suite and the repeatable `scripts/qa/brand_setup_smoke.py` desktop/mobile browser check. The GitHub app-check workflow now runs for pushes to this release branch and saves browser screenshots.
