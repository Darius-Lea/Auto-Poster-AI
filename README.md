# Auto Post

A configurable public-records publishing pipeline. It scrapes arrest/most-wanted listings
from configured sheriff sites, stamps a city logo onto each image, writes a neutral factual caption with
Google Gemini, publishes through a pluggable backend, and sends a Telegram summary after every run.
It is designed to run on GitHub Actions three times daily, around 9:17 a.m.,
1:17 p.m., and 7:17 p.m. Fort Wayne time, adjusting for daylight saving time.
Scheduled runs attempt at most one record each; manual runs can add more. These
are starting times to test, not a claim about Facebook's optimal posting times.
The current configuration uses
the Allen County roster for Fort Wayne. Workspaces share runtime credentials; this is not yet a tenant-isolated subscription app.

The roster scan includes all booking dates and selects the oldest unposted
current listings first. It does not repost a confirmed delivery to the same
Page while its delivery history is retained. Warrant source routing is prepared,
but no warrant website or warrant schedule is enabled until its source is verified.

**Facebook first:** follow [Connect your Facebook Page](docs/FACEBOOK_SETUP.md).
The workflow now has `check-facebook`, `preview`, and `publish` actions. Start with one
post, then enable scheduled Facebook runs with `AUTOPOST_ENABLED=true`.
See the [product roadmap](docs/ROADMAP.md) for the proposed login-based subscription app.

All captions end with: *All individuals are presumed innocent until proven guilty.*
Review each source's terms of use and your local laws on publishing booking photos before going live.

## Local/queue setup checklist

1. Choose public or private repository visibility. Standard GitHub-hosted Actions runners for public repositories are generally free; private repositories have plan-dependent allowances. Other services have their own quotas and terms.
2. Add repository secrets (Settings -> Secrets and variables -> Actions): `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Put each city's logo PNG in `assets/` (names must match `config/cities.json`).
4. Enable the workflow (Actions tab -> *Auto Post* -> Enable).
5. Run **workflow_dispatch** once from the Actions tab.
6. Check Telegram for the run summary.

## Create the Telegram bot

1. Message **@BotFather**, send `/newbot`, follow the prompts, copy the token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` from the response.
4. Save them as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Add a city

Add an object to `config/cities.json` and drop its logo in `assets/`:

```json
{
  "city": "Springfield",
  "workspace_key": "springfield",
  "logo": "assets/springfield.png",
  "sources": [{"adapter": "allen_sheriff", "url": "https://example.com/inmates"}]
}
```

## Add an adapter

1. Create `adapters/my_site.py` with a subclass of `ScraperAdapter` that implements `fetch(url) -> list[Record]`.
2. Register it in `adapters/base.py` (`_REGISTRY`) or call `register_adapter("my_site", "adapters.my_site", "MySiteAdapter")`.
3. Use it in `cities.json` via `"adapter": "my_site"`.

Skip malformed rows with a logged warning; never raise out of `fetch`.

## Choose a backend

Set `PUBLISH_BACKEND` (env var locally, or a repository *variable* in Actions):

| Backend | What it does |
|---|---|
| `queue` (default) | Writes caption + metadata JSON and the stamped image to `outbox/<workspace_key>/`. Uploaded as a workflow artifact. |
| `browser` | Playwright posts through the logged-in account (`BROWSER_PLATFORM=x` or `facebook`) using saved session state. |
| `meta` | Posts a photo to a Facebook Page via the Graph API (`META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN`). |
| `postiz` | Posts to a self-hosted Postiz (`POSTIZ_BASE_URL`, `POSTIZ_API_KEY`). |

## Refresh `STORAGE_STATE_B64` when a session expires

Browser logins are saved as Playwright storage state, not pasted tokens. Social platforms often require
CAPTCHA or 2FA, so create the state on your own machine:

```bash
pip install -r requirements.txt && python -m playwright install chromium
cp .env.example .env   # fill in X_USERNAME / X_PASSWORD (or FACEBOOK_*)
python -m core.auth x  # prints the base64 state
```

Paste the printed string into the `STORAGE_STATE_B64` secret. The workflow decodes it to
`storage_state/combined.json` on each run. Repeat when the session expires.
If login needs a manual step, edit `core/auth.py` to launch with `headless=False`.

## Debug selectors with `--dump-html`

```bash
python main.py --city "Fort Wayne" --dry-run --dump-html
```

This saves `dump_<adapter>.html` in the working directory. Inspect it and update the selectors
marked `# TODO: verify selectors against live HTML` in the adapter.

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env
python main.py --dry-run
```

Flags: `--city`, `--limit` (default 1), `--dry-run` (skips publishing; Telegram message is prefixed `[DRY RUN]`), `--dump-html`, `--check-facebook` (read-only token identity check), `--deliveries`.
Deduplication uses SQLite (`autopost.db`, table `deliveries`) per destination, retaining returned post IDs.
The old `seen` table is preserved but cannot prove delivery to a particular Page; recent records may be eligible on a new destination.
Do not delete database caches to switch backends. Uncertain uploads stay on hold until reconciled; see the Facebook guide.
Actions saves the database even after a partial run fails. Caches can still be evicted, so this is best-effort persistence, not a durable production database.

Run regression tests with `python -m unittest discover -s tests -v`. Facebook tests use fake responses; they do not publish to a live Page.

## Optional: Render (Docker)

`render.yaml` defines a Docker worker. The Render free tier has an **ephemeral filesystem** and sleeps after
15 minutes, so the dedupe DB and outbox do not persist. Use Render only for manual or long-running runs;
GitHub Actions is the primary scheduler.
