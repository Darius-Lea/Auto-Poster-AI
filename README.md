# Auto Post

A multi-city, multi-tenant public-records publishing pipeline. It scrapes arrest/most-wanted listings
from configured sheriff sites, stamps a city logo onto each image, writes a neutral factual caption with
Google Gemini, publishes through a pluggable backend, and sends a Telegram summary after every run.
It is designed to run on GitHub Actions every 30 minutes.

All captions end with: *All individuals are presumed innocent until proven guilty.*
Review each source's terms of use and your local laws on publishing booking photos before going live.

## $0 setup checklist

1. Make the repo **public** (Actions minutes are unlimited for public repos).
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

Flags: `--city`, `--dry-run` (skips publishing; Telegram message is prefixed `[DRY RUN]`), `--dump-html`.
Deduplication uses SQLite (`autopost.db`, table `seen`); the workflow restores it between runs with a cache.

## Optional: Render (Docker)

`render.yaml` defines a Docker worker. The Render free tier has an **ephemeral filesystem** and sleeps after
15 minutes, so the dedupe DB and outbox do not persist. Use Render only for manual or long-running runs;
GitHub Actions is the primary scheduler.
