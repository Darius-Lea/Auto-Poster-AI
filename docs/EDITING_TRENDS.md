# Current video inspiration and editing looks

The private `/clips/trends` endpoint supports YouTube Data API v3's official
`videos.list` `chart=mostPopular` response. The operator supplies
`YOUTUBE_DATA_API_KEY` in service secrets, enables that API for the Google project,
and limits the key to the required API and appropriate server restrictions.
Customers do not create API keys, supply passwords, or paste developer tokens.

Official request contract:
`https://www.googleapis.com/youtube/v3/videos`, `part=snippet,statistics,contentDetails`,
`chart=mostPopular`, `regionCode=<country>`, `maxResults=20`.
Reference: <https://developers.google.com/youtube/v3/docs/videos/list>.
Redirects are refused, TLS verification and the inherited proxy remain enabled,
response bytes/time are bounded, and exceptions/response URLs/bodies are not
reported to customers or stored in the cache. A Google API credential alone does
not establish that the deployed provider operation works.

Register `editing_trends.trends_bp`, call `editing_trends.init()` at app setup,
and include `templates/_editing_trends.html` on the clip page. Its JavaScript
requests the private endpoint, renders text with `textContent`, and only builds
video links from validated YouTube IDs. Choosing a curated look changes the
existing `style` control for the next clip; it does not publish or import video.

The UI shows the source, country and actual last-success time. Failed updates
retain older observations with an explicit stale state. No data or configuration
produces a clear unavailable state and **no invented popular video list**.
Counts unavailable from the provider remain unknown, not zero.

Popular videos are topic/opening inspiration. This data does **not** report which
editing effect caused success, rank trending music, inspect private analytics,
certify a video as a YouTube Short, or predict a customer's engagement. The
standard/bar/minimal looks are curated renderer presets and are always separate
from measured popular-video observations. Full cross-platform editing/effect/
audio trend analysis requires a supported, licensed provider of that data; it is
not supplied by relabelling a public news feed or these counts.

Settings:

- `YOUTUBE_DATA_API_KEY`: operator-only credential; no secret value in source.
- `AUTOPOST_EDITING_TRENDS_ENABLED=false`: optional explicit disable; key presence
  otherwise enables the read-only connector.
- `AUTOPOST_TRENDS_REGION=US`: default two-letter country code. Customer choices
  are US/CA/GB/AU/IN plus this operator-configured default; arbitrary codes cannot
  multiply provider requests across hundreds of cache keys.
- `AUTOPOST_TRENDS_CACHE_SECONDS=1800`: update interval, clamped to 30 minutes–24
  hours. A shared per-country lease and five-minute failure cooldown bound quota
  usage across customer requests. Preview mode never fetches the provider.

Tests use official-shaped fixtures, including cache/failure/freshness/privacy and
unknown-statistic cases. The development environment previously denied provider
destinations and has no YouTube API credential, so a successful live provider
request, live ranking freshness, or live editing-trend analysis is **unverified**.
